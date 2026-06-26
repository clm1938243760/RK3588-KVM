# RK3588 KVM Technical Design

## 1. Purpose

This project is a standalone KVM test program for an ATK-DLRK3588 Debian board.
It is intentionally separate from the larger gateway project. The original
gateway files remain in `/opt/rk3588_gateway`, while this KVM runs from
`/opt/rk3588_kvm`.

The KVM has two jobs:

1. Capture the target computer display from RK3588 HDMI RX.
2. Provide browser-based remote viewing and USB HID keyboard/mouse control.

## 2. Runtime Topology

```text
                  HDMI RX /dev/video40
                         |
                GStreamer / MPP H.264
                         |
                      RTP/UDP
                         |
               MediaMTX
                  |
            WebRTC 1080p
                         |
                     Browser KVM page
                            |
                 /api/mouse and /api/key
                            |
              /dev/hidg1 mouse, /dev/hidg0 keyboard
                            |
                         Target PC
```

The design uses one video capture owner. Two independent programs must not open
`/dev/video40` at the same time. In normal operation `stream_mpp.sh` owns HDMI
RX and Python handles only the browser UI plus HID input.

## 3. Hardware Interfaces

### HDMI RX

The RK3588 board exposes HDMI RX as a V4L2 capture node:

```text
/dev/video40
```

The KVM accepts BGR HDMI RX input and normalizes the browser stream to
1920x1080. The source may be 1080p, 2K, or 4K.

```text
video/x-raw,format=BGR
```

The video path reads the HDMI RX timing and sets `videorate drop-only=true`
to the detected source rate. A 60 FPS input is forwarded at up to 60 FPS, while
a 30 FPS input remains 30 FPS instead of being duplicated into artificial
frames.

### HDMI OUT

HDMI OUT loop-out is deleted from this project. The service does not start
`kmssink`, does not call `modetest`, does not stop `lightdm`, and does not take
over the board desktop display. The KVM output is browser/WebRTC only.

### USB HID

The KVM relies on Linux USB gadget HID nodes:

```text
/dev/hidg0  USB keyboard
/dev/hidg1  USB absolute mouse
```

The old gateway gadget service can be started manually if these nodes do not
exist:

```bash
sudo systemctl start rk3588-usb-printer-gadget.service
```

The keyboard uses the USB HID boot keyboard report format. The mouse uses a
5-byte absolute-position report:

```text
button, x_low, x_high, y_low, y_high
```

Screen coordinates are scaled from `0..1919` and `0..1079` to HID absolute
coordinates `0..32767`.

## 4. Process Model

`run.sh` performs board-side setup:

1. Requires root.
2. Refuses to start if `/dev/video40`, `/dev/hidg0`, or `/dev/hidg1` is absent.
3. Starts MediaMTX for WebRTC signaling and transport.
4. Starts `stream_mpp.sh` as the only `/dev/video40` owner.
5. Starts `rk3588_kvm.py` for the UI and HID API.
6. Writes separate PID and log files under `/tmp`.

`stream_watchdog.sh` supervises the GStreamer worker through MediaMTX's local
path API. It checks both `ready=true` and an increasing `inboundBytes` counter.
Two consecutive failures restart only the stream worker, leaving HID and the
gateway services untouched.

`stop.sh` reads the PID file and terminates the KVM process.

The repository includes `systemd/rk3588-kvm.service` for deployments that should
start KVM on boot. The unit calls `/opt/rk3588_kvm/run.sh` and
`/opt/rk3588_kvm/stop.sh`, and waits for the USB gadget service so `/dev/hidg0`
and `/dev/hidg1` exist before the KVM starts.

## 5. Video Pipeline

The preferred GStreamer pipeline is:

```text
v4l2src device=/dev/video40 io-mode=2 do-timestamp=true
  ! video/x-raw,format=BGR
  ! queue leaky=downstream max-size-buffers=1
  ! videorate drop-only=true max-rate=<detected source fps>
  ! videoscale
  ! video/x-raw,format=BGR,width=1920,height=1080,framerate=<fps>/1
  ! mpph264enc bps=12000000 bps-min=500000 bps-max=24000000 rc-mode=0
    gop=60 qp-init=24 qp-min=16 qp-max=32
    profile=100 level=42 max-reenc=1 qos=true zero-copy-pkt=false
  ! h264parse ! rtph264pay pt=96 aggregate-mode=zero-latency
  ! udpsink port=5004
```

`io-mode=2` is mandatory on the tested board. `io-mode=4` DMA-BUF BGR input
caused `mpp_rkvenc2` IOMMU page faults and a kernel Oops on kernel 5.10.160.
The stable MMAP path sustained a 600-frame encoder benchmark at about 96.9 FPS.

Important latency controls:

```text
queue leaky=downstream      Drop old frames under pressure.
max-size-buffers=1          Keep only one queued frame.
appsink drop=true           Do not build a frame backlog.
appsink max-buffers=1       Cache only the newest frame.
sync=false                  Avoid unnecessary buffering in this test mode.
```

The active MPP path no longer contains a KMS/HDMI OUT branch. This keeps the
remote stream independent from the board desktop display.

## 6. Browser Stream

The web page is served by Python `ThreadingHTTPServer`.

Endpoints:

```text
GET  /              Browser KVM UI
GET  /stream.mjpg   Legacy MJPEG fallback when MediaMTX is unavailable
GET  /api/frame.jpg Legacy JPEG snapshot endpoint when using fallback capture
GET  /api/status    Runtime diagnostics
POST /api/mouse     Absolute mouse event
POST /api/key       Keyboard event
```

The normal browser stream is served by MediaMTX:

```text
H.264 High, level 4.2
1920x1080 output, source-matched frame rate
12 Mbps VBR target, 24 Mbps peak
GOP 60, QP 16-32, one adaptive re-encode attempt
WebRTC HTTP: 8889
WebRTC ICE:  8189 UDP/TCP
```

The Python UI embeds the MediaMTX player while a transparent overlay captures
mouse and keyboard events. In normal systemd operation Python does not open
`/dev/video40`; the capture device is owned by the external MPP H.264 pipeline.

The top-right display control offers two modes:

```text
Fit  Scale the 16:9 picture to the largest browser size that fits.
1:1   Map each 1920x1080 source pixel to one physical display pixel, accounting
      for browser devicePixelRatio, and allow scrolling when it cannot fit.
```

The selected mode is stored in browser local storage.

WebRTC is the only normal video transport. The old MJPEG clarity mode is
intentionally removed from the normal pipeline. This avoids continuous JPEG
encoding, high MJPEG bandwidth, and repeated writes to a frame cache file.

## 7. Keyboard Handling

The browser sends `key`, `code`, and modifier flags. The server maps common
printable ASCII and browser `KeyboardEvent.code` values to USB HID usage IDs.

Supported examples:

```text
Letters, digits, common punctuation
Enter, Escape, Backspace, Tab, Space
Arrow keys, Home, End, PageUp, PageDown
F1-F12
Ctrl, Shift, Alt, Meta modifiers
```

The current keyboard path is intended for normal ASCII/control input. It does
not implement IME-based Chinese text input; for Chinese text on Android or
Windows targets, use the separate ADB/clipboard or PowerShell workflow from the
main gateway experiments.

## 8. Mouse Handling

The browser captures pointer events over the displayed image and converts them
back to the source 1920x1080 coordinate space. The server writes the scaled
absolute coordinates to `/dev/hidg1`.

Supported mouse event types:

```json
{"type":"move","x":960,"y":540}
{"type":"down","x":960,"y":540,"button":0}
{"type":"up","x":960,"y":540,"button":0}
{"type":"dblclick","x":960,"y":540,"button":0}
```

The mouse and keyboard file descriptors are kept open after first use. This
avoids open/close overhead on every pointer movement and lowers input latency.

Mouse movement uses a smooth latest-target policy:

```text
Browser/WebSocket side: keep only the newest move target.
Board/HID side: split large absolute jumps into short 6 ms HID steps.
Click/down/up/double-click: stay ordered and immediate.
```

This preserves low latency while preventing the remote Windows cursor from
visibly jumping during large movements.

## 9. Status API

Example:

```json
{
  "ok": true,
  "video": "/dev/video40",
  "frame_age": 0.01,
  "frame_id": 100,
  "frame_bytes": 50000,
  "capture_fps": 19.8,
  "active_pipeline": "...",
  "last_error": "",
  "keyboard": "/dev/hidg0",
  "keyboard_exists": true,
  "mouse": "/dev/hidg1",
  "mouse_exists": true,
  "mouse_queue_mode": "smooth-latest-target"
}
```

Key diagnostics:

```text
frame_age < 0.2s      Video capture is live.
capture_fps ~= 20     Browser encode loop is healthy.
last_error == ""      Active capture pipeline is stable.
keyboard_exists true  HID keyboard node exists.
mouse_exists true     HID mouse node exists.
```

## 10. Deployment

From a development machine:

```bash
scp -r rk3588_kvm linaro@192.168.20.224:/tmp/rk3588_kvm
ssh linaro@192.168.20.224
sudo rm -rf /opt/rk3588_kvm
sudo mv /tmp/rk3588_kvm /opt/rk3588_kvm
sudo chown -R root:root /opt/rk3588_kvm
sudo chmod 755 /opt/rk3588_kvm/run.sh /opt/rk3588_kvm/stop.sh /opt/rk3588_kvm/rk3588_kvm.py
sudo /opt/rk3588_kvm/run.sh
```

Open:

```text
http://192.168.20.224:8090
```

## 11. Validation Commands

Check devices:

```bash
ls -l /dev/video40 /dev/hidg0 /dev/hidg1
```

Check status:

```bash
curl -s http://127.0.0.1:8090/api/status ; echo
```

Check running process:

```bash
cat /tmp/rk3588_kvm.pid
ps -p "$(cat /tmp/rk3588_kvm.pid)" -o pid,%cpu,%mem,etime,cmd
```

Check logs:

```bash
tail -120 /tmp/rk3588_kvm.log
```

## 12. Known Limits

- This is not hardware HDMI pass-through. It is capture, encode, network
  transport, and HID control in software, so some latency is expected.
- The browser stream is normalized to 1920x1080 to reduce load and keep mouse
  coordinates stable.
- HDMI OUT loop-out is not part of the project.
- Only one process should own `/dev/video40`.
- The included systemd unit assumes the runtime directory is `/opt/rk3588_kvm`.
