# RK3588 KVM

RK3588 lightweight KVM for ATK-DLRK3588 Debian boards.

It reads the target computer through HDMI RX, mirrors the picture to HDMI OUT,
and exposes a browser KVM page for remote viewing plus USB HID keyboard/mouse
control.

## Hardware Path

```text
Target PC HDMI OUT  ->  RK3588 HDMI RX
RK3588 HDMI OUT     ->  Monitor
RK3588 USB gadget   ->  Target PC USB
Browser             ->  http://<rk3588-ip>:8090
```

Verified board defaults:

```text
HDMI RX video node: /dev/video40
Keyboard HID node:  /dev/hidg0
Mouse HID node:     /dev/hidg1
Web port:           8090
Input resolution:   1920x1080@60
Browser stream:     960x540 by default, JPEG quality 68
HDMI mirror:        KMS/kmssink, forced 1920x1080 render rectangle
```

## Files

```text
rk3588_kvm.py   Main KVM server
run.sh          Start script for the board
stop.sh         Stop script for the board
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

## Stop

```bash
sudo /opt/rk3588_kvm/stop.sh
```

The start script stops `lightdm` so KMS can drive HDMI OUT directly. To restore
the Debian desktop:

```bash
sudo systemctl start lightdm
```

## Status API

```bash
curl -s http://127.0.0.1:8090/api/status ; echo
```

Useful fields:

```text
frame_age       Freshness of the latest HDMI frame
capture_fps     Effective capture/encode rate
frame_bytes     Current JPEG frame size for the browser stream
active_pipeline Current GStreamer pipeline
keyboard_exists Whether /dev/hidg0 exists
mouse_exists    Whether /dev/hidg1 exists
```

## After Reboot

The original gateway services can stay disabled while this KVM is tested. If the
HID gadget nodes are missing after reboot, create the gadget manually:

```bash
sudo systemctl start rk3588-usb-printer-gadget.service
sudo /opt/rk3588_kvm/run.sh
```

Do not enable the original gateway service unless you want it to autostart.

## Troubleshooting

If HDMI OUT shows the Debian desktop, `lightdm` is still active:

```bash
sudo systemctl stop lightdm
sudo /opt/rk3588_kvm/run.sh
```

If the picture is stretched, verify that the active pipeline contains:

```text
force-aspect-ratio=false render-rectangle="<0,0,1920,1080>"
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

More details are in [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md).
