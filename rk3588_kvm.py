#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import math
import os
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
KEY_HOLD_SECONDS = 0.008
KEY_RELEASE_SECONDS = 0.006
MOUSE_SETTLE_SECONDS = 0.0
MOUSE_DOUBLE_CLICK_DOWN_SECONDS = 0.035
MOUSE_DOUBLE_CLICK_UP_SECONDS = 0.045
MOUSE_SMOOTH_INTERVAL_SECONDS = 0.006
MOUSE_SMOOTH_DIRECT_DISTANCE = 48.0
MOUSE_SMOOTH_MAX_STEP = 180.0
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

MOD_CODE_TO_BIT = {
    "ControlLeft": 0x01,
    "ShiftLeft": 0x02,
    "AltLeft": 0x04,
    "MetaLeft": 0x08,
    "ControlRight": 0x10,
    "ShiftRight": 0x20,
    "AltRight": 0x40,
    "MetaRight": 0x80,
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
        '<video id="video" class="video-layer" autoplay muted playsinline disablepictureinpicture></video>'
        if webrtc
        else '<img id="video" class="video-layer" src="/stream.mjpg" draggable="false" />'
    )
    webrtc_setup = (
        """
const video = document.getElementById("video");
let currentWebRTCStream = null;
let readerScriptLoading = false;
let smoothVideoRequested = false;
video.addEventListener("playing", () => {
  video.classList.add("is-playing");
});

function connectLowLatencyWebRTC() {
  if (!smoothVideoRequested || webrtcReader || typeof MediaMTXWebRTCReader !== "function") return;
  const base = "http://" + location.hostname + ":8889/kvm/";
  webrtcReader = new MediaMTXWebRTCReader({
    url: base + "whep",
    onError: err => { state.textContent = "WebRTC: " + err; },
    onTrack: ev => {
      try { ev.receiver.playoutDelayHint = 0; } catch (e) {}
      try { ev.receiver.jitterBufferTarget = 0; } catch (e) {}
      const stream = ev.streams && ev.streams[0] ? ev.streams[0] : new MediaStream([ev.track]);
      if (currentWebRTCStream !== stream) {
        currentWebRTCStream = stream;
        video.srcObject = stream;
      }
      video.play().catch(err => { state.textContent = "video play: " + err; });
    },
    onDataChannel: () => {}
  });
}

function startLowLatencyWebRTC() {
  smoothVideoRequested = true;
  if (webrtcReader) return;
  if (typeof MediaMTXWebRTCReader === "function") {
    connectLowLatencyWebRTC();
    return;
  }
  if (readerScriptLoading) return;
  readerScriptLoading = true;
  const base = "http://" + location.hostname + ":8889/kvm/";
  const script = document.createElement("script");
  script.src = base + "reader.js";
  script.onload = () => {
    readerScriptLoading = false;
    connectLowLatencyWebRTC();
  };
  script.onerror = () => {
    readerScriptLoading = false;
    state.textContent = "failed to load WebRTC reader";
  };
  document.head.appendChild(script);
}

function stopLowLatencyWebRTC() {
  smoothVideoRequested = false;
  if (webrtcReader) webrtcReader.close();
  webrtcReader = null;
  currentWebRTCStream = null;
  video.pause();
  video.srcObject = null;
  video.classList.remove("is-playing");
}
"""
        if webrtc
        else """
const video = document.getElementById("video");
function startLowLatencyWebRTC() {}
function stopLowLatencyWebRTC() {}
"""
    )
    body = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RK3588 KVM</title>
<style>
html, body {{ margin: 0; height: 100%; background: #000; color: #e5e7eb; font-family: Arial, sans-serif; overflow: hidden; overscroll-behavior: none; }}
#stage {{ height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden; }}
#viewport {{ position: relative; width: min(100vw, calc(100vh * 16 / 9)); aspect-ratio: 16 / 9; background: #000; overflow: hidden; }}
#stage.native {{ overflow: auto; }}
#stage.native #viewport {{ flex: 0 0 auto; width: var(--native-width); height: var(--native-height); aspect-ratio: auto; margin: auto; }}
.video-layer, #screen {{ position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }}
.video-layer {{ object-fit: contain; pointer-events: none; background: #000; transform: translateZ(0); backface-visibility: hidden; }}
.video-layer.is-hidden {{ display: none; }}
video.video-layer {{ opacity: 0; }}
video.video-layer.is-playing {{ opacity: 1; }}
#screen {{ outline: none; user-select: none; -webkit-user-select: none; touch-action: none; cursor: default; z-index: 2; }}
#state {{ display: none; }}
#controls {{ position: fixed; top: 10px; right: 10px; z-index: 20; display: flex; gap: 6px; opacity: 0.38; transition: opacity 120ms ease; }}
#controls:hover, #controls:focus-within {{ opacity: 1; }}
.segmented {{ display: flex; padding: 2px; gap: 2px; background: rgba(17, 24, 39, 0.72); border: 1px solid rgba(255, 255, 255, 0.24); border-radius: 6px; }}
.mode-button {{ min-width: 48px; height: 28px; padding: 0 9px; border: 0; border-radius: 4px; background: transparent; color: #d1d5db; font-size: 13px; cursor: pointer; }}
.mode-button.active {{ background: #f3f4f6; color: #111827; }}
</style>
</head>
<body>
<span id="state">video {width}x{height}, click image then type</span>
<div id="controls">
  <div id="displayMode" class="segmented" role="group" aria-label="Display mode">
    <button class="mode-button" type="button" data-mode="fit">Fit</button>
    <button class="mode-button" type="button" data-mode="native">1:1</button>
  </div>
</div>
<div id="stage" class="fit">
  <div id="viewport">
    {media}
    <div id="screen" tabindex="0"></div>
  </div>
</div>
<script>
const W = {width};
const H = {height};
const EVT_MOVE = {MOUSE_EVENT_MOVE};
const EVT_DOWN = {MOUSE_EVENT_DOWN};
const EVT_UP = {MOUSE_EVENT_UP};
const EVT_DBLCLICK = {MOUSE_EVENT_DBLCLICK};
const stage = document.getElementById("stage");
const viewport = document.getElementById("viewport");
const displayModeButtons = Array.from(document.querySelectorAll("[data-mode]"));
const DISPLAY_MODE_KEY = "rk3588-kvm-display-mode";
let webrtcReader = null;
{webrtc_setup}
const img = document.getElementById("screen");
const state = document.getElementById("state");
let mouseSocket = null;
let mouseSocketTimer = null;
let heartbeatTimer = null;
let pendingMove = null;
let moveTimer = null;

function updateNativeSize() {{
  const dpr = Math.max(window.devicePixelRatio || 1, 1);
  document.documentElement.style.setProperty("--native-width", (W / dpr) + "px");
  document.documentElement.style.setProperty("--native-height", (H / dpr) + "px");
}}

function setDisplayMode(mode, persist = true) {{
  const nextMode = mode === "native" ? "native" : "fit";
  updateNativeSize();
  stage.classList.toggle("native", nextMode === "native");
  stage.classList.toggle("fit", nextMode === "fit");
  displayModeButtons.forEach(button => {{
    const active = button.dataset.mode === nextMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  }});
  if (persist) localStorage.setItem(DISPLAY_MODE_KEY, nextMode);
  window.requestAnimationFrame(() => img.focus());
}}

displayModeButtons.forEach(button => {{
  button.addEventListener("click", ev => {{
    ev.preventDefault();
    ev.stopPropagation();
    setDisplayMode(button.dataset.mode);
  }});
}});
window.addEventListener("resize", updateNativeSize);
setDisplayMode(localStorage.getItem(DISPLAY_MODE_KEY) || "fit", false);

{("startLowLatencyWebRTC();" if webrtc else "")}

function coords(ev) {{
  const r = img.getBoundingClientRect();
  return {{
    x: Math.max(0, Math.min(W - 1, Math.round((ev.clientX - r.left) * W / r.width))),
    y: Math.max(0, Math.min(H - 1, Math.round((ev.clientY - r.top) * H / r.height)))
  }};
}}

function updateCursor(ev) {{
  return coords(ev);
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

function startHeartbeat() {{
  if (heartbeatTimer) return;
  heartbeatTimer = window.setInterval(() => {{
    if (mouseSocket && mouseSocket.readyState === WebSocket.OPEN && mouseSocket.bufferedAmount < 1024) {{
      mouseSocket.send('{{"type":"ping"}}');
    }}
  }}, 15000);
}}

function ensureMouseSocket() {{
  if (mouseSocket && (mouseSocket.readyState === WebSocket.OPEN || mouseSocket.readyState === WebSocket.CONNECTING)) return;
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  mouseSocket = new WebSocket(scheme + location.host + "/ws/input");
  mouseSocket.binaryType = "arraybuffer";
  mouseSocket.onopen = () => {{
    state.textContent = "video {width}x{height}, websocket smooth input ready";
    startHeartbeat();
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
  const packet = mousePacket(type, button, x, y);
  if (mouseSocket && mouseSocket.readyState === WebSocket.OPEN) {{
    if (type === EVT_MOVE && mouseSocket.bufferedAmount > 0) return;
    mouseSocket.send(packet);
    return;
  }}
  mouseFallback(type, button, x, y);
}}

function sendKey(payload) {{
  if (mouseSocket && mouseSocket.readyState === WebSocket.OPEN && mouseSocket.bufferedAmount < 4096) {{
    mouseSocket.send(JSON.stringify({{ type: "key", ...payload }}));
    return;
  }}
  post("/api/key", payload);
}}

function drainMouseMove() {{
  moveTimer = null;
  if (!pendingMove) return;
  if (mouseSocket && mouseSocket.readyState === WebSocket.OPEN && mouseSocket.bufferedAmount > 0) {{
    moveTimer = window.setTimeout(drainMouseMove, 2);
    return;
  }}
  const move = pendingMove;
  pendingMove = null;
  sendMouse(EVT_MOVE, move.buttons, move.x, move.y);
  if (pendingMove) queueMoveSend();
}}

function queueMoveSend() {{
  if (moveTimer !== null) return;
  moveTimer = window.setTimeout(drainMouseMove, 0);
}}

function handlePointerMove(ev) {{
  const c = updateCursor(ev);
  pendingMove = {{ ...c, buttons: ev.buttons }};
  queueMoveSend();
}}

const moveEventName = ("onpointerrawupdate" in window) ? "pointerrawupdate" : "pointermove";
img.addEventListener(moveEventName, handlePointerMove, {{ passive: true }});

img.addEventListener("pointerdown", ev => {{
  img.focus();
  ev.preventDefault();
  pendingMove = null;
  const c = updateCursor(ev);
  try {{ img.setPointerCapture(ev.pointerId); }} catch (e) {{}}
  sendMouse(EVT_DOWN, ev.button, c.x, c.y);
}});

img.addEventListener("pointerup", ev => {{
  ev.preventDefault();
  pendingMove = null;
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
window.addEventListener("keydown", ev => {{
  if (document.activeElement !== img) return;
  ev.preventDefault();
  sendKey({{
    action: "down",
    key: ev.key,
    code: ev.code,
    ctrl: ev.ctrlKey,
    alt: ev.altKey,
    shift: ev.shiftKey,
    meta: ev.metaKey
  }});
}});

window.addEventListener("keyup", ev => {{
  if (document.activeElement !== img) return;
  ev.preventDefault();
  sendKey({{
    action: "up",
    key: ev.key,
    code: ev.code,
    ctrl: ev.ctrlKey,
    alt: ev.altKey,
    shift: ev.shiftKey,
    meta: ev.metaKey
  }});
}});

window.addEventListener("blur", () => {{
  sendKey({{ action: "reset" }});
}});
window.addEventListener("beforeunload", () => sendKey({{ action: "reset" }}));

ensureMouseSocket();
img.focus();

window.addEventListener("beforeunload", () => {{
  if (webrtcReader) webrtcReader.close();
}});
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
        caps = f"video/x-raw,format=BGR,width={self.width},height={self.height}"
        sink = "appsink drop=true max-buffers=1 sync=false"
        queue = "queue leaky=downstream max-size-buffers=1 max-size-time=0 max-size-bytes=0"
        return [
            f"v4l2src device={self.device} io-mode=4 do-timestamp=true ! {caps} ! {queue} ! {sink}",
            f"v4l2src device={self.device} do-timestamp=true ! {caps} ! {queue} ! {sink}",
            f"v4l2src device={self.device} ! {caps} ! videoconvert ! {queue} ! {sink}",
        ]

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
                f"video/x-raw,format=BGR,width={self.width},height={self.height}",
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


class ExternalStreamGrabber:
    def __init__(self) -> None:
        self._started_at = time.monotonic()

    def latest(self) -> tuple[int, Optional[bytes]]:
        return 0, None

    def latest_full_jpeg(self, quality: int | None = None) -> tuple[int, Optional[bytes], float | None]:
        return 0, None, None

    def status(self) -> dict[str, Any]:
        return {
            "frame_age": None,
            "frame_id": 0,
            "frame_bytes": 0,
            "capture_fps": None,
            "active_pipeline": "external MPP H264 WebRTC",
            "last_error": "",
            "stream_uptime": round(time.monotonic() - self._started_at, 2),
        }

    def close(self) -> None:
        return


class MouseInputDispatcher:
    def __init__(self, hid: "HidDevices") -> None:
        self.hid = hid
        self._condition = threading.Condition()
        self._actions: deque[tuple[int, int, int, int]] = deque()
        self._latest_move: Optional[tuple[int, int, int, int]] = None
        self._last_mouse: Optional[tuple[int, int, int]] = None
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
            self._latest_move = None
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
                "mouse_queue_mode": "smooth-latest-target",
                "mouse_action_backlog": len(self._actions),
                "mouse_has_pending_move": self._latest_move is not None,
                "mouse_latest_move": latest_move,
                "mouse_last_position": None if self._last_mouse is None else {
                    "buttons": self._last_mouse[0],
                    "x": self._last_mouse[1],
                    "y": self._last_mouse[2],
                },
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
            event: tuple[int, int, int, int] | None = None
            smooth_wait = False
            with self._condition:
                while not self._stop and not self._actions and self._latest_move is None:
                    self._condition.wait(timeout=0.5)
                if self._stop:
                    return
                if self._actions:
                    event = self._actions.popleft()
                    self._record_action_position_locked(*event)
                else:
                    event = self._next_smooth_move_locked()
                    smooth_wait = self._latest_move is not None
            if event is not None:
                try:
                    self._dispatch(*event)
                except OSError as exc:
                    print(f"mouse dispatch error: {exc}", flush=True)
                    time.sleep(0.02)
            if smooth_wait:
                time.sleep(MOUSE_SMOOTH_INTERVAL_SECONDS)

    def _record_action_position_locked(self, event_type: int, button: int, x: int, y: int) -> None:
        if event_type == MOUSE_EVENT_DOWN:
            self._last_mouse = (button & 0x07, x, y)
        elif event_type == MOUSE_EVENT_UP:
            self._last_mouse = (0, x, y)
        elif event_type == MOUSE_EVENT_DBLCLICK:
            self._last_mouse = (0, x, y)

    def _next_smooth_move_locked(self) -> tuple[int, int, int, int] | None:
        if self._latest_move is None:
            return None
        _, buttons, target_x, target_y = self._latest_move
        buttons &= 0x07

        if self._last_mouse is None:
            self._last_mouse = (buttons, target_x, target_y)
            self._latest_move = None
            return (MOUSE_EVENT_MOVE, buttons, target_x, target_y)

        _, last_x, last_y = self._last_mouse
        dx = target_x - last_x
        dy = target_y - last_y
        distance = math.hypot(dx, dy)
        if distance <= MOUSE_SMOOTH_DIRECT_DISTANCE:
            next_x = target_x
            next_y = target_y
            self._latest_move = None
        else:
            scale = min(1.0, MOUSE_SMOOTH_MAX_STEP / distance)
            next_x = clamp_int(round(last_x + dx * scale), 0, self.hid.width - 1)
            next_y = clamp_int(round(last_y + dy * scale), 0, self.hid.height - 1)
            if next_x == last_x and target_x != last_x:
                next_x += 1 if target_x > last_x else -1
            if next_y == last_y and target_y != last_y:
                next_y += 1 if target_y > last_y else -1
            next_x = clamp_int(next_x, 0, self.hid.width - 1)
            next_y = clamp_int(next_y, 0, self.hid.height - 1)
            self._latest_move = (MOUSE_EVENT_MOVE, buttons, target_x, target_y)
        self._last_mouse = (buttons, next_x, next_y)
        return (MOUSE_EVENT_MOVE, buttons, next_x, next_y)

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
        self._pressed_modifiers: dict[str, int] = {}
        self._pressed_usages: dict[str, int] = {}

    def status(self) -> dict[str, Any]:
        return {
            "keyboard": self.keyboard,
            "keyboard_exists": Path(self.keyboard).exists(),
            "mouse": self.mouse,
            "mouse_exists": Path(self.mouse).exists(),
        }

    def close(self) -> None:
        with self._keyboard_lock:
            self._release_all_keys_locked()
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

    def _write_keyboard_locked(self, report: bytes) -> None:
        self._write_with_retry(self._keyboard_fd_locked, self._close_keyboard_locked, report)

    @staticmethod
    def _payload_modifiers(payload: dict[str, Any]) -> int:
        mod = 0
        if payload.get("ctrl"):
            mod |= 0x01
        if payload.get("shift"):
            mod |= 0x02
        if payload.get("alt"):
            mod |= 0x04
        if payload.get("meta"):
            mod |= 0x08
        return mod

    @staticmethod
    def _key_id(payload: dict[str, Any]) -> str:
        code_name = str(payload.get("code") or "")
        key = str(payload.get("key") or "")
        return code_name or key

    @staticmethod
    def _usage_from_payload(payload: dict[str, Any]) -> tuple[int, int]:
        code_name = str(payload.get("code") or "")
        key = str(payload.get("key") or "")
        usage = CODE_TO_USAGE.get(code_name)
        if usage is not None:
            return 0, usage
        if len(key) == 1:
            return KEY.get(key, (0, 0))
        return 0, 0

    def _keyboard_report(self, extra_mod: int = 0) -> bytes:
        mod = extra_mod
        for value in self._pressed_modifiers.values():
            mod |= value
        usages = list(dict.fromkeys(self._pressed_usages.values()))[:6]
        usages.extend([0] * (6 - len(usages)))
        return bytes([mod & 0xFF, 0, *[usage & 0xFF for usage in usages]])

    def _send_keyboard_state_locked(self, extra_mod: int = 0) -> None:
        self._write_keyboard_locked(self._keyboard_report(extra_mod))

    def _release_all_keys_locked(self) -> None:
        self._pressed_modifiers.clear()
        self._pressed_usages.clear()
        try:
            self._write_keyboard_locked(bytes(8))
        except OSError:
            pass

    def press_key(self, mod: int, code: int) -> None:
        self._write_keyboard(bytes([mod & 0xFF, 0, code & 0xFF, 0, 0, 0, 0, 0]))
        time.sleep(KEY_HOLD_SECONDS)
        self._write_keyboard(bytes(8))
        time.sleep(KEY_RELEASE_SECONDS)

    def key_event(self, payload: dict[str, Any]) -> bool:
        action = str(payload.get("action") or "press")
        if action in ("down", "up", "reset"):
            return self.key_state_event(payload, action)

        key = str(payload.get("key") or "")
        code_name = str(payload.get("code") or "")
        mod = self._payload_modifiers(payload)

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

    def key_state_event(self, payload: dict[str, Any], action: str) -> bool:
        key_id = self._key_id(payload)
        if action == "reset":
            with self._keyboard_lock:
                self._release_all_keys_locked()
            return True
        if not key_id:
            return False

        with self._keyboard_lock:
            mod_bit = MOD_CODE_TO_BIT.get(key_id)
            if mod_bit is not None:
                if action == "down":
                    self._pressed_modifiers[key_id] = mod_bit
                else:
                    self._pressed_modifiers.pop(key_id, None)
                self._send_keyboard_state_locked()
                return True

            key_mod, usage = self._usage_from_payload(payload)
            if not usage:
                return False
            if action == "down":
                self._pressed_usages[key_id] = usage
                self._send_keyboard_state_locked(self._payload_modifiers(payload) | key_mod)
            else:
                self._pressed_usages.pop(key_id, None)
                self._send_keyboard_state_locked(self._payload_modifiers(payload) & ~key_mod)
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
        delay_seconds: float = 0.002,
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
        if args.external_stream:
            self.grabber = ExternalStreamGrabber()
        elif args.frame_file:
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
            self.connection.settimeout(300.0)
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
                if opcode == 0x1:
                    if not self._handle_input_text_frame(payload):
                        self._ws_send_frame(0x8, b"")
                        return
                    continue
                if opcode != 0x2:
                    continue
                if not self.state.mouse_input.enqueue_packet(payload):
                    self._ws_send_frame(0x8, b"")
                    return
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

    def _handle_input_text_frame(self, payload: bytes) -> bool:
        if len(payload) > 4096:
            return False
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(message, dict):
            return False
        if message.get("type") == "ping":
            return True
        if message.get("type") == "key":
            self.state.hid.key_event(message)
            return True
        return False

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
    parser.add_argument("--external-stream", action="store_true")
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
