#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

SCREEN_W = 1920
SCREEN_H = 1080
KEY_HOLD_SECONDS = 0.025
KEY_RELEASE_SECONDS = 0.025
MOUSE_SETTLE_SECONDS = 0.0

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


def html_page(width: int, height: int) -> bytes:
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
#screen {{ max-width: 100%; max-height: 100%; object-fit: contain; outline: none; user-select: none; cursor: crosshair; }}
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
  <img id="screen" src="/stream.mjpg" tabindex="0" draggable="false" />
</div>
<script>
const W = {width};
const H = {height};
const img = document.getElementById("screen");
const state = document.getElementById("state");
let lastMove = 0;

function coords(ev) {{
  const r = img.getBoundingClientRect();
  return {{
    x: Math.max(0, Math.min(W - 1, Math.round((ev.clientX - r.left) * W / r.width))),
    y: Math.max(0, Math.min(H - 1, Math.round((ev.clientY - r.top) * H / r.height)))
  }};
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

img.addEventListener("pointermove", ev => {{
  const now = performance.now();
  if (now - lastMove < 16) return;
  lastMove = now;
  post("/api/mouse", {{ type: "move", ...coords(ev), buttons: ev.buttons }});
}});

img.addEventListener("pointerdown", ev => {{
  img.focus();
  ev.preventDefault();
  post("/api/mouse", {{ type: "down", ...coords(ev), button: ev.button }});
}});

img.addEventListener("pointerup", ev => {{
  ev.preventDefault();
  post("/api/mouse", {{ type: "up", ...coords(ev), button: ev.button }});
}});

img.addEventListener("dblclick", ev => {{
  ev.preventDefault();
  post("/api/mouse", {{ type: "dblclick", ...coords(ev), button: ev.button }});
}});

img.addEventListener("contextmenu", ev => ev.preventDefault());

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
                self._store(tmp.read_bytes())
                with self._lock:
                    self._active_pipeline = "gst-launch snapshot fallback"
                    self._last_error = ""
            except Exception:
                with self._lock:
                    self._last_error = "gst-launch snapshot fallback failed"
                time.sleep(1.0)
            self._stop.wait(frame_interval)

    def _encode_store(self, frame: np.ndarray) -> None:
        if self.scale_width and 0 < self.scale_width < frame.shape[1]:
            new_h = int(frame.shape[0] * self.scale_width / frame.shape[1])
            frame = cv2.resize(frame, (self.scale_width, new_h), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if ok:
            self._store(encoded.tobytes())


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
            self._keyboard_fd = os.open(self.keyboard, os.O_RDWR | getattr(os, "O_NONBLOCK", 0))
        return self._keyboard_fd

    def _mouse_fd_locked(self) -> int:
        if self._mouse_fd is None:
            self._mouse_fd = os.open(self.mouse, os.O_WRONLY | getattr(os, "O_NONBLOCK", 0))
        return self._mouse_fd

    def _write_keyboard(self, report: bytes) -> None:
        with self._keyboard_lock:
            try:
                os.write(self._keyboard_fd_locked(), report)
            except OSError:
                self._close_keyboard_locked()
                os.write(self._keyboard_fd_locked(), report)

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
            try:
                os.write(self._mouse_fd_locked(), self._mouse_report(button, x, y))
            except OSError:
                self._close_mouse_locked()
                os.write(self._mouse_fd_locked(), self._mouse_report(button, x, y))

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
        self.grabber = FrameGrabber(
            device=args.video,
            width=args.width,
            height=args.height,
            fps=args.fps,
            quality=args.quality,
            scale_width=args.scale_width,
        )
        self.hid = HidDevices(args.keyboard, args.mouse, args.width, args.height)

    def close(self) -> None:
        self.grabber.close()
        self.hid.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "RK3588KVM/0.1"

    @property
    def state(self) -> KvmState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path == "/stream.mjpg" or self.path.startswith("/api/mouse"):
            return
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            body = html_page(self.state.args.width, self.state.args.height)
            self._send_bytes(body, "text/html; charset=utf-8")
            return
        if self.path == "/stream.mjpg":
            self._stream()
            return
        if self.path == "/api/status":
            status = {
                "ok": True,
                "video": self.state.args.video,
                **self.state.grabber.status(),
                **self.state.hid.status(),
            }
            self._send_json(status)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/api/mouse":
                self.state.hid.mouse_event(payload)
                self._send_json({"ok": True})
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

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

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


class KvmServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[Handler], state: KvmState) -> None:
        super().__init__(address, handler)
        self.state = state


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
