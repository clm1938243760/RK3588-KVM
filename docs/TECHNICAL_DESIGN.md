# RK3588 KVM Technical Design

## 1. Purpose

This project is a standalone KVM test program for an ATK-DLRK3588 Debian board.
It is intentionally separate from the larger gateway project. The original
gateway files remain in `/opt/rk3588_gateway`, while this KVM runs from
`/opt/rk3588_kvm`.

The KVM has three jobs:

1. Capture the target computer display from RK3588 HDMI RX.
2. Mirror that display to RK3588 HDMI OUT.
3. Provide browser-based remote viewing and USB HID keyboard/mouse control.

## 2. Runtime Topology

```text
                  HDMI RX /dev/video40
                         |
                    GStreamer tee
             /            |              \
     KMS HDMI OUT    MPP H.264 encode    JPEG 2 FPS
                         |               snapshot
                    MPEG-TS/UDP             |
                         |          /api/frame.jpg
                      MediaMTX
                         |
                    WebRTC 1080p60
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
`/dev/video40` at the same time. The server therefore captures once and splits
the stream internally.

## 3. Hardware Interfaces

### HDMI RX

The RK3588 board exposes HDMI RX as a V4L2 capture node:

```text
/dev/video40
```

The verified capture format is:

```text
video/x-raw,format=BGR,width=1920,height=1080,framerate=60/1
```

The KVM requests this format in every primary pipeline.

### HDMI OUT

HDMI OUT is driven through GStreamer `kmssink`. The startup script stops
`lightdm` before launching the KVM so that the KMS sink can own the display
plane.

The sink connector is selected automatically from `modetest -c`: the code picks
the last connected `HDMI-A-*` connector. On the tested board this was connector
`224`, corresponding to `HDMI-A-2`.

The final mirror sink is intentionally forced to the rectangle that tested
correctly on the monitor:

```text
kmssink sync=false fullscreen=true force-aspect-ratio=false
force-modesetting=true render-rectangle="<0,0,1920,1080>"
connector-id=<connected HDMI connector>
```

Although `kmssink` reports `display-width=1024` and `display-height=768` on this
system, the forced render rectangle produced the correct visible aspect ratio
during testing.

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
3. Stops `lightdm` so HDMI OUT can be driven by KMS.
4. Starts MediaMTX for WebRTC signaling and transport.
5. Starts `stream_mpp.sh` as the only `/dev/video40` owner.
6. Starts `rk3588_kvm.py` for the UI, HID and snapshot API.
7. Writes separate PID and log files under `/tmp`.

`stream_watchdog.sh` supervises the GStreamer worker through MediaMTX's local
path API. It checks both `ready=true` and an increasing `inboundBytes` counter.
Two consecutive failures restart only the stream worker, leaving HID and the
gateway services untouched.

`stop.sh` reads the PID file and terminates the KVM process.

The KVM is not installed as an autostart service by default. This is deliberate:
it is a manual test tool that should not interfere with the original gateway
unless explicitly started.

## 5. Video Pipeline

The preferred GStreamer pipeline is:

```text
v4l2src device=/dev/video40 io-mode=2 do-timestamp=true
  ! video/x-raw,format=BGR,width=1920,height=1080,framerate=60/1
  ! tee name=t
    t. ! queue leaky=downstream max-size-buffers=2
       ! mpph264enc bps=8000000 gop=30 profile=66 level=42
         zero-copy-pkt=false
       ! h264parse ! mpegtsmux ! udpsink port=5000
    t. ! queue leaky=downstream max-size-buffers=1
       ! videorate max-rate=2 ! jpegenc
       ! multifilesink location=/tmp/rk3588_kvm_latest.jpg
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
sync=false                  Avoid display-clock buffering in this test mode.
```

If the mirror pipeline fails, the code tries fallback capture pipelines without
the mirror branch so the web KVM can still come up.

## 6. Browser Stream

The web page is served by Python `ThreadingHTTPServer`.

Endpoints:

```text
GET  /              Browser KVM UI
GET  /stream.mjpg   Legacy MJPEG fallback
GET  /api/frame.jpg Latest 1920x1080 vision snapshot
GET  /api/status    Runtime diagnostics
POST /api/mouse     Absolute mouse event
POST /api/key       Keyboard event
```

The normal browser stream is served by MediaMTX:

```text
H.264 baseline, level 4.2
1920x1080 input at 60 FPS
8 Mbps CBR target
WebRTC HTTP: 8889
WebRTC ICE:  8189 UDP/TCP
```

The Python UI embeds the MediaMTX player while a transparent overlay captures
mouse and keyboard events. The bridge vision path continues to use a separate
full-resolution JPEG snapshot without opening `/dev/video40`.

The stream handler tracks `frame_id` and only sends a new JPEG when the capture
thread has encoded a new frame. This prevents duplicate-frame loops from wasting
network bandwidth.

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
  "mouse_exists": true
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

Check HDMI connectors:

```bash
modetest -c | grep -E "HDMI-A|connected"
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

Restore the Debian desktop:

```bash
sudo /opt/rk3588_kvm/stop.sh
sudo systemctl start lightdm
```

## 12. Known Limits

- This is not hardware HDMI pass-through. It is capture, render, and HID control
  in software, so some latency is expected.
- The browser stream is downscaled by default to reduce CPU and network load.
- HDMI OUT aspect ratio depends on the tested KMS render rectangle. The current
  stable setting is the forced 1920x1080 rectangle.
- Only one process should own `/dev/video40`.
- The script is manual-start only and does not create a systemd autostart unit.
