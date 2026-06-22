#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

import cv2
import numpy as np

SCREEN_W = 1920
SCREEN_H = 1080
KEY_HOLD_SECONDS = 0.025
KEY_RELEASE_SECONDS = 0.025
MOUSE_SETTLE_SECONDS = 0.0
MOUSE_DOUBLE_CLICK_DOWN_SECONDS = 0.05
MOUSE_DOUBLE_CLICK_UP_SECONDS = 0.08
MOUSE_WS_PACKET_SIZE = 6
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MOUSE_EVENT_MOVE = 0
MOUSE_EVENT_DOWN = 1
MOUSE_EVENT_UP = 2
MOUSE_EVENT_DBLCLICK = 3

KEY: dict[str, tuple[int, int]] = {
    "\n": (0, 0x28),
    "\t": (0, 0x2B),
    " ": (0, 0x2C),
    "-": (0, 0x2D),
    "=": (0, 0x2E),
    "[": (0, 0x2F),
    "]": (0, 0x30),
    "\\": (0, 0x31),
    ";": (0, 0x33),
    "'": (0, 0x34),
    "`": (0, 0x35),
    ",": (0, 0x36),
    ".": (0, 0x37),
    "/": (0, 0x38),
    "!": (0x02, 0x1E),
    "@": (0x02, 0x1F),
    "#": (0x02, 0x20),
    "$": (0x02, 0x21),
    "%": (0x02, 0x22),
    "^": (0x02, 0x23),
    "&": (0x02, 0x24),
    "*": (0x02, 0x25),
    "(": (0x02, 0x26),
    ")": (0x02, 0x27),
    "_": (0x02, 0x2D),
    "+": (0x02, 0x2E),
    "{": (0x02, 0x2F),
    "}": (0x02, 0x30),
    "|": (0x02, 0x31),
    ":": (0x02, 0x33),
    '"': (0x02, 0x34),
    "~": (0x02, 0x35),
    "<": (0x02, 0x36),
    ">": (0x02, 0x37),
    "?": (0x02, 0x38),
}

for i in range(10):
    KEY[str(i)] = (0, 0x27 if i == 0 else 0x1D + i)
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEY[ch] = (0, 0x04 + i)
    KEY[ch.upper()] = (0x02, 0x04 + i)

CODE_TO_USAGE = {
    "Enter": 0x28,
    "Escape": 0x29,
    "Backspace": 0x2A,
    "Tab": 0x2B,
    "Space": 0x2C,
    "Minus": 0x2D,
    "Equal": 0x2E,
    "BracketLeft": 0x2F,
    "BracketRight": 0x30,
    "Backslash": 0x31,
    "Semicolon": 0x33,
    "Quote": 0x34,
    "Backquote": 0x35,
    "Comma": 0x36,
    "Period": 0x37,
    "Slash": 0x38,
    "CapsLock": 0x39,
    "F1": 0x3A,
    "F2": 0x3B,
    "F3": 0x3C,
    "F4": 0x3D,
    "F5": 0x3E,
    "F6": 0x3F,
    "F7": 0x40,
    "F8": 0x41,
    "F9": 0x42,
    "F10": 0x43,
    "F11": 0x44,
    "F12": 0x45,
    "PrintScreen": 0x46,
    "ScrollLock": 0x47,
    "Pause": 0x48,
    "Insert": 0x49,
    "Home": 0x4A,
    "PageUp": 0x4B,
    "Delete": 0x4C,
    "End": 0x4D,
    "PageDown": 0x4E,
    "ArrowRight": 0x4F,
    "ArrowLeft": 0x50,
    "ArrowDown": 0x51,
    "ArrowUp": 0x52,
}

for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    CODE_TO_USAGE[f"Key{ch}"] = 0x04 + i
for i in range(10):
    CODE_TO_USAGE[f"Digit{i}"] = 0x27 if i == 0 else 0x1D + i


def clamp_int(value: Any, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = low
    return max(low, min(high, parsed))


def html_page(width: int, height: int, webrtc: bool = False) -> bytes:
    media = (
        '<iframe id="video" class="video-layer" allow="autoplay; fullscreen" scrolling="no"></iframe>'
        if webrtc
        else '<img id="video" class="video-layer" src="/stream.mjpg" draggable="false" />'
    )
    webrtc_setup = (
        'document.getElementById("video").src = "http://" + location.hostname + ":8889/kvm?autoplay=true&muted=true&controls=false";'
        if webrtc
        else ""
    )
    body = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RK3588 KVM</title>
<style>
html, body {{ margin: 0; height: 100%; background: #111827; color: #e5e7eb; font-family: Arial, sans-serif; }}
#top {{ height: 42px; display: flex; align-items: center; gap: 16px; padding: 0 14px; background: #0f172a; }}
#stage {{ height: calc(100% - 42px); display: flex; align-items: center; justify-content: center; overflow: hidden; }}
#viewport {{ position: relative; width: min(100%, calc((100vh - 42px) * 16 / 9)); aspect-ratio: 16 / 9; }}
.video-layer, #screen {{ position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }}
.video-layer {{ object-fit: contain; pointer-events: none; background: #000; }}
#screen {{ outline: none; user-select: none; cursor: crosshair; z-index: 2; }}
#cursor {{ position: absolute; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.95); border-radius: 999px; box-shadow: 0 0 0 1px rgba(15,23,42,0.7); pointer-events: none; z-index: 3; transform: translate(-50%, -50%) scale(1); opacity: 0; transition: opacity 120ms linear, transform 90ms ease-out; }}
#cursor.active {{ opacity: 1; }}
#cursor.clicking {{ transform: translate(-50%, -50%) scale(0.86); }}
button {{ background: #2563eb; color: white; border: 0; border-radius: 4px; padding: 7px 10px; }}
#state {{ color: #9ca3af; font-size: 13px; }}
</style>
</head>
<body>
<div id="top">
  <strong>RK3588 KVM</strong>
  <button id="focus">Focus Keyboard</button>
  <span id="state">video {width}x{height}, click image then type</span>
</div>
<div id="stage">
  <div id="viewport">
    {media}
    <div id="screen" tabindex="0"></div>
    <div id="cursor"></div>
  </div>
</div>
<script>
const W = {width};
const H = {height};
const EVT_MOVE = {MOUSE_EVENT_MOVE};
const EVT_DOWN = {MOUSE_EVENT_DOWN};
const EVT_UP = {MOUSE_EVENT_UP};
const EVT_DBLCLICK = {MOUSE_EVENT_DBLCLICK};
{webrtc_setup}
const img = document.getElementById("screen");
const state = document.getElementById("state");
const cursor = document.getElementById("cursor");
let mouseSocket = null;
let mouseSocketTimer = null;
let pendingMove = null;
let moveDrainQueued = false;
let predictedCursor = null;
let clickPulseTimer = null;

function coords(ev) {{
  const r = img.getBoundingClientRect();
  return {{
    x: Math.max(0, Math.min(W - 1, Math.round((ev.clientX - r.left) * W / r.width))),
    y: Math.max(0, Math.min(H - 1, Math.round((ev.clientY - r.top) * H / r.height)))
  }};
}}

function viewportPosition(ev) {{
  const r = img.getBoundingClientRect();
  return {{
    left: Math.max(0, Math.min(r.width, ev.clientX - r.left)),
    top: Math.max(0, Math.min(r.height, ev.clientY - r.top))
  }};
}}

function viewportPositionFromCoords(x, y) {{
  const r = img.getBoundingClientRect();
  return {{
    left: Math.max(0, Math.min(r.width, x * r.width / Math.max(W - 1, 1))),
    top: Math.max(0, Math.min(r.height, y * r.height / Math.max(H - 1, 1)))
  }};
}}

function paintCursorAt(x, y) {{
  const pos = viewportPositionFromCoords(x, y);
  cursor.style.left = pos.left + "px";
  cursor.style.top = pos.top + "px";
  cursor.classList.add("active");
}}

function updateCursor(ev) {{
  const c = coords(ev);
  predictedCursor = c;
  paintCursorAt(c.x, c.y);
  return c;
}}

function pulseCursor() {{
  cursor.classList.add("clicking");
  if (clickPulseTimer) window.clearTimeout(clickPulseTimer);
  clickPulseTimer = window.setTimeout(() => {{
    cursor.classList.remove("clicking");
    clickPulseTimer = null;
  }}, 90);
}}

async function post(path, payload) {{
  try {{
    const res = await fetch(path, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(payload)
    }});
    if (!res.ok) state.textContent = path + " HTTP " + res.status;
  }} catch (e) {{
    state.textContent = "request failed: " + e;
  }}
}}

function mousePacket(type, button, x, y) {{
  const buf = new ArrayBuffer(6);
  const view = new DataView(buf);
  view.setUint8(0, type);
  view.setUint8(1, button & 0xff);
  view.setUint16(2, x, true);
  view.setUint16(4, y, true);
  return buf;
}}

function mouseFallback(type, button, x, y) {{
  const payload = {{ x, y, button }};
  if (type === EVT_DOWN) payload.type = "down";
  else if (type === EVT_UP) payload.type = "up";
  else if (type === EVT_DBLCLICK) payload.type = "dblclick";
  else payload.type = "move";
  post("/api/mouse", payload);
}}

function scheduleMouseSocketReconnect(delayMs = 1000) {{
  if (mouseSocketTimer) return;
  mouseSocketTimer = window.setTimeout(() => {{
    mouseSocketTimer = null;
    ensureMouseSocket();
  }}, delayMs);
}}

function ensureMouseSocket() {{
  if (mouseSocket && (mouseSocket.readyState === WebSocket.OPEN || mouseSocket.readyState === WebSocket.CONNECTING)) return;
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  mouseSocket = new WebSocket(scheme + location.host + "/ws/input");
  mouseSocket.binaryType = "arraybuffer";
  mouseSocket.onopen = () => {{
    state.textContent = "video {width}x{height}, websocket latest-only input ready";
  }};
  mouseSocket.onclose = () => {{
    state.textContent = "input reconnecting...";
    scheduleMouseSocketReconnect();
  }};
  mouseSocket.onerror = () => {{
    if (mouseSocket && mouseSocket.readyState !== WebSocket.OPEN) {{
      state.textContent = "input websocket unavailable, fallback active";
    }}
  }};
}}

function sendMouse(type, button, x, y) {{
  predictedCursor = {{ x, y }};
  paintCursorAt(x, y);
  if (type !== EVT_MOVE) pulseCursor();
  const packet = mousePacket(type, button, x, y);
  if (mouseSocket && mouseSocket.readyState === WebSocket.OPEN) {{
    if (type === EVT_MOVE && mouseSocket.bufferedAmount > 1024) return;
    mouseSocket.send(packet);
    return;
  }}
  mouseFallback(type, button, x, y);
}}

function queueMoveSend() {{
  if (moveDrainQueued) return;
  moveDrainQueued = true;
  requestAnimationFrame(() => {{
    moveDrainQueued = false;
    if (!pendingMove) return;
    const move = pendingMove;
    pendingMove = null;
    sendMouse(EVT_MOVE, move.buttons, move.x, move.y);
  }});
}}

img.addEventListener("pointermove", ev => {{
  const c = updateCursor(ev);
  pendingMove = {{ ...c, buttons: ev.buttons }};
  queueMoveSend();
}});

img.addEventListener("pointerdown", ev => {{
  img.focus();
  ev.preventDefault();
  const c = updateCursor(ev);
  try {{ img.setPointerCapture(ev.pointerId); }} catch (e) {{}}
  sendMouse(EVT_DOWN, ev.button, c.x, c.y);
}});

img.addEventListener("pointerup", ev => {{
  ev.preventDefault();
  const c = updateCursor(ev);
  try {{ img.releasePointerCapture(ev.pointerId); }} catch (e) {{}}
  sendMouse(EVT_UP, ev.button, c.x, c.y);
}});

img.addEventListener("dblclick", ev => {{
  ev.preventDefault();
  const c = updateCursor(ev);
  sendMouse(EVT_DBLCLICK, ev.button, c.x, c.y);
}});

img.addEventListener("contextmenu", ev => ev.preventDefault());
img.addEventListener("pointerleave", () => {{
  if (!predictedCursor) cursor.classList.remove("active");
}});
window.addEventListener("blur", () => cursor.classList.remove("active"));

window.addEventListener("keydown", ev => {{
  if (document.activeElement !== img) return;
  if (ev.repeat) return;
  ev.preventDefault();
  post("/api/key", {{
    key: ev.key,
    code: ev.code,
    ctrl: ev.ctrlKey,
    alt: ev.altKey,
    shift: ev.shiftKey,
    meta: ev.metaKey
  }});
}});

document.getElementById("focus").onclick = () => img.focus();
ensureMouseSocket();
img.focus();
</script>
</body>
</html>
"""
    return body.encode("utf-8")


class FrameGrabber:
    def __init__(self, device: str, width: int, height: int, fps: int, quality: int, scale_width: int) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.fps = max(1, fps)
        self.quality = max(40, min(95, quality))
        self.scale_width = scale_width
        self._latest: Optional[bytes] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_at = 0.0
        self._frame_id = 0
        self._frame_count = 0
        self._started_at = time.monotonic()
        self._last_error = ""
        self._active_pipeline = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def latest(self) -> tuple[int, Optional[bytes]]:
        with self._lock:
            return self._frame_id, self._latest

    def latest_full_jpeg(self, quality: int | None = None) -> tuple[int, Optional[bytes], float | None]:
        with self._lock:
            frame_id = self._frame_id
            frame = None if self._latest_frame is None else self._latest_frame.copy()
            age = None if not self._latest_at else time.monotonic() - self._latest_at
        if frame is None:
            return frame_id, None, age
        encode_quality = max(40, min(95, quality if quality is not None else 90))
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), encode_quality])
        if not ok:
            return frame_id, None, age
        return frame_id, encoded.tobytes(), age

    def status(self) -> dict[str, Any]:
        with self._lock:
            age = None if not self._latest_at else time.monotonic() - self._latest_at
            uptime = max(time.monotonic() - self._started_at, 0.001)
            return {
                "frame_age": age,
                "frame_id": self._frame_id,
                "frame_bytes": len(self._latest or b""),
                "capture_fps": round(self._frame_count / uptime, 2),
                "active_pipeline": self._active_pipeline,
                "last_error": self._last_error,
            }

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _store(self, jpeg: bytes) -> None:
        with self._lock:
            self._latest = jpeg
            self._latest_at = time.monotonic()
            self._frame_id += 1
            self._frame_count += 1

    def _store_frame_and_preview(self, frame: np.ndarray, preview_jpeg: bytes) -> None:
        with self._lock:
            self._latest_frame = frame.copy()
            self._latest = preview_jpeg
            self._latest_at = time.monotonic()
            self._frame_id += 1
            self._frame_count += 1

    def _pipelines(self) -> list[str]:
        caps = f"video/x-raw,format=BGR,width={self.width},height={self.height},framerate=60/1"
        sink = "appsink drop=true max-buffers=1 sync=false"
        queue = "queue leaky=downstream max-size-buffers=1 max-size-time=0 max-size-bytes=0"
        mirror_sink = self._mirror_sink()
        mirror_caps = f"video/x-raw,width={self.width},height={self.height},pixel-aspect-ratio=1/1"
        mirror = f"{queue} ! videoconvert ! videoscale ! {mirror_caps} ! {mirror_sink}"
        return [
            f"v4l2src device={self.device} io-mode=4 do-timestamp=true ! {caps} ! tee name=t "
            f"t. ! {mirror} t. ! {queue} ! {sink}",
            f"v4l2src device={self.device} do-timestamp=true ! {caps} ! tee name=t "
            f"t. ! {mirror} t. ! {queue} ! {sink}",
            f"v4l2src device={self.device} io-mode=4 do-timestamp=true ! {caps} ! {queue} ! {sink}",
            f"v4l2src device={self.device} do-timestamp=true ! {caps} ! {queue} ! {sink}",
            f"v4l2src device={self.device} ! {caps} ! videoconvert ! {queue} ! {sink}",
        ]

    def _mirror_sink(self) -> str:
        connector_id = self._find_hdmi_connector_id()
        base = (
            "kmssink sync=false fullscreen=true force-aspect-ratio=false "
            "force-modesetting=true render-rectangle=\"<0,0,1920,1080>\""
        )
        if connector_id is not None:
            return f"{base} connector-id={connector_id}"
        return base

    @staticmethod
    def _find_hdmi_connector_id() -> Optional[int]:
        try:
            result = subprocess.run(
                ["modetest", "-c"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
        except Exception:
            return None
        connected: list[int] = []
        fallback: list[int] = []
        for line in result.stdout.splitlines():
            match = re.match(r"^\s*(\d+)\s+\d+\s+(\w+)\s+(HDMI-A-\d+)\s+", line)
            if not match:
                continue
            connector_id = int(match.group(1))
            status = match.group(2)
            fallback.append(connector_id)
            if status == "connected":
                connected.append(connector_id)
        if connected:
            return connected[-1]
        if fallback:
            return fallback[-1]
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            for pipeline in self._pipelines():
                if self._stop.is_set():
                    return
                if self._opencv_loop(pipeline):
                    break
            else:
                self._gst_snapshot_loop()

    def _opencv_loop(self, pipeline: str) -> bool:
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            with self._lock:
                self._last_error = f"failed to open pipeline: {pipeline}"
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        frame_interval = 1.0 / self.fps
        got_frame = False
        try:
            while not self._stop.is_set():
                frame_started_at = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    with self._lock:
                        self._last_error = "capture read failed"
                    break
                got_frame = True
                with self._lock:
                    self._active_pipeline = pipeline
                    self._last_error = ""
                self._encode_store(frame)
                elapsed = time.monotonic() - frame_started_at
                if elapsed < frame_interval:
                    self._stop.wait(frame_interval - elapsed)
            return got_frame
        finally:
            cap.release()

    def _gst_snapshot_loop(self) -> None:
        frame_interval = 1.0 / max(1, min(self.fps, 2))
        tmp = Path("/tmp/rk3588_kvm_frame.jpg")
        while not self._stop.is_set():
            cmd = [
                "gst-launch-1.0",
                "-q",
                "-e",
                "v4l2src",
                f"device={self.device}",
                "num-buffers=1",
                "!",
                f"video/x-raw,format=BGR,width={self.width},height={self.height},framerate=60/1",
                "!",
                "videoconvert",
                "!",
                "jpegenc",
                f"quality={self.quality}",
                "!",
                "filesink",
                f"location={tmp}",
            ]
            try:
                subprocess.run(cmd, check=True, timeout=4)
                jpeg = tmp.read_bytes()
                frame = cv2.imread(str(tmp))
                if frame is not None:
                    self._store_frame_and_preview(frame, jpeg)
                else:
                    self._store(jpeg)
                with self._lock:
                    self._active_pipeline = "gst-launch snapshot fallback"
                    self._last_error = ""
            except Exception:
                with self._lock:
                    self._last_error = "gst-launch snapshot fallback failed"
                time.sleep(1.0)
            self._stop.wait(frame_interval)

    def _encode_store(self, frame: np.ndarray) -> None:
        preview = frame
        if self.scale_width and 0 < self.scale_width < frame.shape[1]:
            new_h = int(frame.shape[0] * self.scale_width / frame.shape[1])
            preview = cv2.resize(frame, (self.scale_width, new_h), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if ok:
            self._store_frame_and_preview(frame, encoded.tobytes())


class ExternalFrameGrabber:
    def __init__(self, frame_file: str) -> None:
        self.frame_file = Path(frame_file)
        self._frame_id = 0
        self._last_mtime_ns = 0
        self._latest: Optional[bytes] = None
        self._latest_at = 0.0
        self._started_at = time.monotonic()
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        try:
            stat = self.frame_file.stat()
            if stat.st_mtime_ns == self._last_mtime_ns:
                return
            data = self.frame_file.read_bytes()
            if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                return
        except (FileNotFoundError, OSError):
            return
        with self._lock:
            self._latest = data
            self._latest_at = time.monotonic()
            self._last_mtime_ns = stat.st_mtime_ns
            self._frame_id += 1

    def latest(self) -> tuple[int, Optional[bytes]]:
        self._refresh()
        with self._lock:
            return self._frame_id, self._latest

    def latest_full_jpeg(self, quality: int | None = None) -> tuple[int, Optional[bytes], float | None]:
        self._refresh()
        with self._lock:
            age = None if not self._latest_at else time.monotonic() - self._latest_at
            return self._frame_id, self._latest, age

    def status(self) -> dict[str, Any]:
        self._refresh()
        with self._lock:
            age = None if not self._latest_at else time.monotonic() - self._latest_at
            return {
                "frame_age": age,
                "frame_id": self._frame_id,
                "frame_bytes": len(self._latest or b""),
                "capture_fps": None,
                "active_pipeline": "external MPP H264/WebRTC",
                "last_error": "" if self._latest is not None else f"frame file not ready: {self.frame_file}",
            }

    def close(self) -> None:
        return


class MouseInputDispatcher:
    def __init__(self, hid: "HidDevices") -> None:
        self.hid = hid
        self._condition = threading.Condition()
        self._actions: deque[tuple[int, int, int, int]] = deque()
        self._latest_move: Optional[tuple[int, int, int, int]] = None
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        self._thread.join(timeout=2.0)

    def queue_move(self, buttons: int, x: int, y: int) -> None:
        with self._condition:
            self._latest_move = (MOUSE_EVENT_MOVE, buttons & 0x07, x, y)
            self._condition.notify()

    def queue_action(self, event_type: int, button: int, x: int, y: int) -> None:
        with self._condition:
            self._actions.append((event_type, button & 0x07, x, y))
            self._condition.notify()

    def status(self) -> dict[str, Any]:
        with self._condition:
            latest_move = None if self._latest_move is None else {
                "x": self._latest_move[2],
                "y": self._latest_move[3],
                "buttons": self._latest_move[1],
            }
            return {
                "mouse_queue_mode": "latest-only-move",
                "mouse_action_backlog": len(self._actions),
                "mouse_has_pending_move": self._latest_move is not None,
                "mouse_latest_move": latest_move,
            }

    def enqueue_packet(self, payload: bytes) -> bool:
        event = decode_mouse_packet(payload, self.hid.width, self.hid.height)
        return self._enqueue_event(event)

    def enqueue_payload(self, payload: dict[str, Any]) -> bool:
        event_type = str(payload.get("type") or "move")
        x = clamp_int(payload.get("x"), 0, self.hid.width - 1)
        y = clamp_int(payload.get("y"), 0, self.hid.height - 1)
        button = clamp_int(payload.get("button"), 0, 2)
        if event_type == "down":
            event = (MOUSE_EVENT_DOWN, HidDevices._button_mask(button), x, y)
        elif event_type == "up":
            event = (MOUSE_EVENT_UP, 0, x, y)
        elif event_type == "dblclick":
            event = (MOUSE_EVENT_DBLCLICK, HidDevices._button_mask(button), x, y)
        else:
            event = (MOUSE_EVENT_MOVE, clamp_int(payload.get("buttons"), 0, 7), x, y)
        return self._enqueue_event(event)

    def _enqueue_event(self, event: tuple[int, int, int, int] | None) -> bool:
        if event is None:
            return False
        event_type, button, x, y = event
        if event_type == MOUSE_EVENT_MOVE:
            self.queue_move(button, x, y)
        else:
            self.queue_action(event_type, button, x, y)
        return True

    def _loop(self) -> None:
        while True:
            with self._condition:
                while not self._stop and not self._actions and self._latest_move is None:
                    self._condition.wait(timeout=0.5)
                if self._stop:
                    return
                if self._actions:
                    event = self._actions.popleft()
                else:
                    event = self._latest_move
                    self._latest_move = None
            if event is not None:
                try:
                    self._dispatch(*event)
                except OSError as exc:
                    print(f"mouse dispatch error: {exc}", flush=True)
                    time.sleep(0.02)

    def _dispatch(self, event_type: int, button: int, x: int, y: int) -> None:
        if event_type == MOUSE_EVENT_DOWN:
            self.hid.write_mouse(button, x, y)
            return
        if event_type == MOUSE_EVENT_UP:
            self.hid.write_mouse(0, x, y)
            return
        if event_type == MOUSE_EVENT_DBLCLICK:
            for _ in range(2):
                self.hid.write_mouse(button, x, y)
                time.sleep(MOUSE_DOUBLE_CLICK_DOWN_SECONDS)
                self.hid.write_mouse(0, x, y)
                time.sleep(MOUSE_DOUBLE_CLICK_UP_SECONDS)
            return
        self.hid.write_mouse(0, x, y)
        time.sleep(MOUSE_SETTLE_SECONDS)


def decode_mouse_packet(payload: bytes, width: int, height: int) -> tuple[int, int, int, int] | None:
    if len(payload) != MOUSE_WS_PACKET_SIZE:
        return None
    event_type, button, x, y = struct.unpack("<BBHH", payload)
    if event_type not in (MOUSE_EVENT_MOVE, MOUSE_EVENT_DOWN, MOUSE_EVENT_UP, MOUSE_EVENT_DBLCLICK):
        return None
    x = clamp_int(x, 0, width - 1)
    y = clamp_int(y, 0, height - 1)
    if event_type == MOUSE_EVENT_MOVE:
        return event_type, button & 0x07, x, y
    return event_type, HidDevices._button_mask(clamp_int(button, 0, 2)), x, y


class HidDevices:
    def __init__(self, keyboard: str, mouse: str, width: int, height: int) -> None:
        self.keyboard = keyboard
        self.mouse = mouse
        self.width = width
        self.height = height
        self._keyboard_lock = threading.Lock()
        self._mouse_lock = threading.Lock()
        self._keyboard_fd: Optional[int] = None
        self._mouse_fd: Optional[int] = None

    def status(self) -> dict[str, Any]:
        return {
            "keyboard": self.keyboard,
            "keyboard_exists": Path(self.keyboard).exists(),
            "mouse": self.mouse,
            "mouse_exists": Path(self.mouse).exists(),
        }

    def close(self) -> None:
        with self._keyboard_lock:
            self._close_keyboard_locked()
        with self._mouse_lock:
            self._close_mouse_locked()

    def _close_keyboard_locked(self) -> None:
        if self._keyboard_fd is not None:
            try:
                os.close(self._keyboard_fd)
            finally:
                self._keyboard_fd = None

    def _close_mouse_locked(self) -> None:
        if self._mouse_fd is not None:
            try:
                os.close(self._mouse_fd)
            finally:
                self._mouse_fd = None

    def _keyboard_fd_locked(self) -> int:
        if self._keyboard_fd is None:
            self._keyboard_fd = os.open(self.keyboard, os.O_RDWR)
        return self._keyboard_fd

    def _mouse_fd_locked(self) -> int:
        if self._mouse_fd is None:
            self._mouse_fd = os.open(self.mouse, os.O_WRONLY)
        return self._mouse_fd

    def _write_keyboard(self, report: bytes) -> None:
        with self._keyboard_lock:
            self._write_with_retry(self._keyboard_fd_locked, self._close_keyboard_locked, report)

    def press_key(self, mod: int, code: int) -> None:
        self._write_keyboard(bytes([mod & 0xFF, 0, code & 0xFF, 0, 0, 0, 0, 0]))
        time.sleep(KEY_HOLD_SECONDS)
        self._write_keyboard(bytes(8))
        time.sleep(KEY_RELEASE_SECONDS)

    def key_event(self, payload: dict[str, Any]) -> bool:
        key = str(payload.get("key") or "")
        code_name = str(payload.get("code") or "")
        mod = 0
        if payload.get("ctrl"):
            mod |= 0x01
        if payload.get("shift"):
            mod |= 0x02
        if payload.get("alt"):
            mod |= 0x04
        if payload.get("meta"):
            mod |= 0x08

        if len(key) == 1 and not (payload.get("ctrl") or payload.get("alt") or payload.get("meta")):
            key_mod, usage = KEY.get(key, (0, 0))
            if not usage:
                return False
            self.press_key(key_mod, usage)
            return True

        usage = CODE_TO_USAGE.get(code_name)
        if usage is None:
            return False
        self.press_key(mod, usage)
        return True

    @staticmethod
    def _scale(value: int, size: int) -> int:
        return max(0, min(32767, int(value * 32767 / max(size - 1, 1))))

    @staticmethod
    def _button_mask(button: int) -> int:
        if button == 2:
            return 0x02
        if button == 1:
            return 0x04
        return 0x01

    def _mouse_report(self, button: int, x: int, y: int) -> bytes:
        ax = self._scale(x, self.width)
        ay = self._scale(y, self.height)
        return bytes([button & 0x07, ax & 0xFF, (ax >> 8) & 0xFF, ay & 0xFF, (ay >> 8) & 0xFF])

    def write_mouse(self, button: int, x: int, y: int) -> None:
        with self._mouse_lock:
            self._write_with_retry(self._mouse_fd_locked, self._close_mouse_locked, self._mouse_report(button, x, y))

    @staticmethod
    def _write_with_retry(
        open_fd: Any,
        close_fd: Any,
        report: bytes,
        retries: int = 4,
        delay_seconds: float = 0.01,
    ) -> None:
        last_error: OSError | None = None
        for _ in range(retries):
            try:
                os.write(open_fd(), report)
                return
            except BlockingIOError as exc:
                last_error = exc
                time.sleep(delay_seconds)
            except OSError as exc:
                last_error = exc
                close_fd()
                if exc.errno in (errno.EAGAIN, errno.EBUSY):
                    time.sleep(delay_seconds)
                    continue
                if exc.errno in (errno.EPIPE, errno.ENODEV, errno.ESHUTDOWN, errno.EIO, errno.EBADF, errno.ENXIO):
                    time.sleep(delay_seconds)
                    continue
                raise
        if last_error is not None:
            raise last_error

    def mouse_event(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or "move")
        x = clamp_int(payload.get("x"), 0, self.width - 1)
        y = clamp_int(payload.get("y"), 0, self.height - 1)
        button = self._button_mask(clamp_int(payload.get("button"), 0, 2))
        if event_type == "down":
            self.write_mouse(button, x, y)
        elif event_type == "up":
            self.write_mouse(0, x, y)
        elif event_type == "dblclick":
            for _ in range(2):
                self.write_mouse(button, x, y)
                time.sleep(0.05)
                self.write_mouse(0, x, y)
                time.sleep(0.08)
        else:
            self.write_mouse(0, x, y)
            time.sleep(MOUSE_SETTLE_SECONDS)


class KvmState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        if args.frame_file:
            self.grabber = ExternalFrameGrabber(args.frame_file)
        else:
            self.grabber = FrameGrabber(
                device=args.video,
                width=args.width,
                height=args.height,
                fps=args.fps,
                quality=args.quality,
                scale_width=args.scale_width,
            )
        self.hid = HidDevices(args.keyboard, args.mouse, args.width, args.height)
        self.mouse_input = MouseInputDispatcher(self.hid)

    def close(self) -> None:
        self.mouse_input.close()
        self.grabber.close()
        self.hid.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "RK3588KVM/0.1"

    @property
    def state(self) -> KvmState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        path = urlsplit(self.path).path
        if path == "/stream.mjpg" or path.startswith("/api/mouse") or path == "/ws/input":
            return
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/ws/input":
            self._handle_input_websocket()
            return
        if path == "/":
            body = html_page(self.state.args.width, self.state.args.height, self.state.args.webrtc)
            self._send_bytes(body, "text/html; charset=utf-8")
            return
        if path == "/stream.mjpg":
            self._stream()
            return
        if path == "/api/status":
            status = {
                "ok": True,
                "video": self.state.args.video,
                **self.state.grabber.status(),
                **self.state.hid.status(),
                **self.state.mouse_input.status(),
            }
            self._send_json(status)
            return
        if path == "/api/frame.jpg":
            query = parse_qs(parsed.query)
            quality = self._parse_quality(query.get("quality", ["90"])[0])
            frame_id, frame, age = self.state.grabber.latest_full_jpeg(quality)
            if frame is None:
                self._send_json({"ok": False, "error": "frame not ready"}, status=503)
                return
            headers = {"X-Frame-Id": str(frame_id)}
            if age is not None:
                headers["X-Frame-Age"] = f"{age:.3f}"
            self._send_bytes(frame, "image/jpeg", headers=headers)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/api/mouse":
                ok = self.state.mouse_input.enqueue_payload(payload)
                self._send_json({"ok": ok})
                return
            if self.path == "/api/key":
                ok = self.state.hid.key_event(payload)
                self._send_json({"ok": ok})
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 65536:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _parse_quality(value: str) -> int:
        try:
            return max(40, min(95, int(value)))
        except (TypeError, ValueError):
            return 90

    def _stream(self) -> None:
        boundary = "rk3588kvm"
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        last_frame_id = -1
        while True:
            frame_id, frame = self.state.grabber.latest()
            if frame is None:
                time.sleep(0.02)
                continue
            if frame_id == last_frame_id:
                time.sleep(0.005)
                continue
            try:
                self.wfile.write(f"--{boundary}\r\n".encode("ascii"))
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                last_frame_id = frame_id
            except (BrokenPipeError, ConnectionResetError):
                return

    def _handle_input_websocket(self) -> None:
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(HTTPStatus.BAD_REQUEST, "expected websocket upgrade")
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(HTTPStatus.BAD_REQUEST, "missing websocket key")
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        try:
            self.connection.settimeout(30.0)
            while True:
                opcode, payload = self._ws_read_frame()
                if opcode is None:
                    return
                if opcode == 0x8:
                    self._ws_send_frame(0x8, payload)
                    return
                if opcode == 0x9:
                    self._ws_send_frame(0xA, payload)
                    continue
                if opcode != 0x2:
                    continue
                if not self.state.mouse_input.enqueue_packet(payload):
                    self._ws_send_frame(0x8, b"")
                    return
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

    def _ws_read_frame(self) -> tuple[int | None, bytes]:
        header = self.rfile.read(2)
        if len(header) < 2:
            return None, b""
        first, second = header
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            ext = self.rfile.read(2)
            if len(ext) < 2:
                return None, b""
            length = struct.unpack(">H", ext)[0]
        elif length == 127:
            ext = self.rfile.read(8)
            if len(ext) < 8:
                return None, b""
            length = struct.unpack(">Q", ext)[0]
        mask = self.rfile.read(4) if masked else b""
        if masked and len(mask) < 4:
            return None, b""
        payload = self.rfile.read(length)
        if len(payload) < length:
            return None, b""
        if masked:
            payload = bytes(item ^ mask[i % 4] for i, item in enumerate(payload))
        return opcode, payload

    def _ws_send_frame(self, opcode: int, payload: bytes = b"") -> None:
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        self.connection.sendall(bytes(header) + payload)


class KvmServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[Handler], state: KvmState) -> None:
        super().__init__(address, handler)
        self.state = state

    def get_request(self) -> tuple[socket.socket, Any]:
        request, client_address = super().get_request()
        try:
            request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return request, client_address


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RK3588 lightweight HDMI-RX USB-HID KVM")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--video", default="/dev/video40")
    parser.add_argument("--keyboard", default="/dev/hidg0")
    parser.add_argument("--mouse", default="/dev/hidg1")
    parser.add_argument("--width", type=int, default=SCREEN_W)
    parser.add_argument("--height", type=int, default=SCREEN_H)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--quality", type=int, default=68)
    parser.add_argument("--scale-width", type=int, default=960)
    parser.add_argument("--frame-file", default="")
    parser.add_argument("--webrtc", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = KvmState(args)
    server = KvmServer((args.host, args.port), Handler, state)
    print(f"RK3588 KVM listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
