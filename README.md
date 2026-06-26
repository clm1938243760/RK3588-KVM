# RK3588 KVM

RK3588 lightweight KVM for ATK-DLRK3588 Debian boards.

It reads the target computer through HDMI RX and exposes a browser KVM page for
remote viewing plus USB HID keyboard/mouse control.

## Hardware Path

```text
Target PC HDMI OUT  ->  RK3588 HDMI RX
RK3588 USB gadget   ->  Target PC USB
Browser             ->  http://<rk3588-ip>:8090
```

Verified board defaults:

```text
HDMI RX video node: /dev/video40
Keyboard HID node:  /dev/hidg0
Mouse HID node:     /dev/hidg1
Web port:           8090
Input signal:       HDMI RX source timing, commonly 1920x1080@30/60
Browser stream:     1920x1080 H.264 High WebRTC, source-matched FPS, 12/24 Mbps VBR
Mouse input path:   Persistent WebSocket, smooth latest-target queue
```

## Files

```text
rk3588_kvm.py   Main KVM server
stream_mpp.sh   HDMI RX and MPP H.264 pipeline
stream_watchdog.sh  Restarts the encoder if MediaMTX input bytes stop growing
mediamtx.yml    WebRTC gateway configuration
install_mediamtx.sh  MediaMTX ARM64 installer
run.sh          Start script for the board
stop.sh         Stop script for the board
systemd/        Optional boot service unit
docs/           Technical documentation
```

## Start

Copy the directory to the board as `/opt/rk3588_kvm`, then run:

```bash
sudo /opt/rk3588_kvm/run.sh
```

Open the browser page:

```text
http://192.168.20.224:8090
```

Click the video once before typing so the web page receives keyboard focus.
Use the top-right `Fit / 1:1` control to switch between fitting the browser and
physical-pixel 1:1 display. The page remembers the selected mode.

## Stop

```bash
sudo /opt/rk3588_kvm/stop.sh
```

## Status API

```bash
curl -s http://127.0.0.1:8090/api/status ; echo
```

The WebRTC player is also available directly at:

```text
http://192.168.20.224:8889/kvm
```

Useful fields:

```text
frame_age       Null in external H.264 mode
capture_fps     Null in external H.264 mode
frame_bytes     Zero in external H.264 mode
active_pipeline Current external stream mode
keyboard_exists Whether /dev/hidg0 exists
mouse_exists    Whether /dev/hidg1 exists
mouse_queue_mode Current mouse dispatch policy
mouse_action_backlog Pending click/down/up actions
mouse_has_pending_move Whether one latest move is waiting
```

## After Reboot

The repository includes an optional systemd unit for boot startup. Install it as
`/etc/systemd/system/rk3588-kvm.service`, then enable it when the board should
start KVM automatically:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rk3588-kvm.service
```

If the HID gadget nodes are missing after reboot, create the gadget manually:

```bash
sudo systemctl start rk3588-usb-printer-gadget.service
sudo /opt/rk3588_kvm/run.sh
```

Do not enable the original gateway service unless you also want BRIDGE to
autostart.

## Troubleshooting

If the picture is stretched, verify that the active pipeline contains:

```text
video/x-raw,format=BGR,width=1920,height=1080
```

If the web page works but keyboard or mouse does not, check:

```bash
ls -l /dev/hidg0 /dev/hidg1
```

If HDMI capture is busy or missing:

```bash
ls -l /dev/video40
fuser /dev/video40 2>/dev/null || true
```

Do not change the hardware stream back to `io-mode=4`. On the tested 5.10.160
kernel, DMA-BUF BGR input to `mpph264enc` caused an RK MPP IOMMU page fault and
kernel Oops. The verified stable path is `io-mode=2` with
`zero-copy-pkt=false`.

The stream watchdog checks the local MediaMTX path API every five seconds. If
the path is not ready or `inboundBytes` stops increasing twice in succession,
it restarts only the GStreamer encoder worker. HID, BRIDGE and the KVM HTTP
server remain online.

The browser page uses a persistent WebSocket for mouse input. Move events stay
latest-only on the network side to avoid backlog. The board then smooths large
absolute-mouse jumps into short HID steps, while button down/up/double-click
actions stay ordered and immediate.

More details are in:

- [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md)
