#!/bin/bash
set -euo pipefail

VIDEO_DEVICE="${VIDEO_DEVICE:-/dev/video40}"
FRAME_FILE="${FRAME_FILE:-/tmp/rk3588_kvm_latest.jpg}"
UDP_PORT="${UDP_PORT:-5000}"
BITRATE="${BITRATE:-8000000}"
JPEG_FPS="${JPEG_FPS:-2}"
ENCODER="${ENCODER:-mpp}"

rm -f "$FRAME_FILE"

PIPELINE=(
  gst-launch-1.0 -q -e
  v4l2src "device=$VIDEO_DEVICE" io-mode=2 do-timestamp=true !
  "video/x-raw,format=BGR,width=1920,height=1080,framerate=60/1" !
  videorate drop-only=true max-rate=60 ! "video/x-raw,framerate=60/1" !
  tee name=t
)

CONNECTOR_ID="$(modetest -c 2>/dev/null | awk '$3 == "connected" && $4 ~ /^HDMI-A-/ {id=$1} END {print id}')"
if [[ -n "$CONNECTOR_ID" ]]; then
  PIPELINE+=(
    t. ! queue leaky=downstream max-size-buffers=1 max-size-time=0 max-size-bytes=0 !
    videoconvert ! videoscale !
    "video/x-raw,width=1920,height=1080,pixel-aspect-ratio=1/1" !
    kmssink sync=false fullscreen=true force-aspect-ratio=false
    force-modesetting=true "render-rectangle=<0,0,1920,1080>" "connector-id=$CONNECTOR_ID"
  )
else
  echo "No connected HDMI OUT; WebRTC streaming continues without KMS mirror"
fi

PIPELINE+=(t. ! queue leaky=downstream max-size-buffers=1 max-size-time=0 max-size-bytes=0 !)
if [[ "$ENCODER" == "mpp" ]]; then
  PIPELINE+=(
    mpph264enc "bps=$BITRATE" "bps-min=$BITRATE" "bps-max=$BITRATE"
    gop=15 profile=66 level=42 header-mode=1 rc-mode=1 zero-copy-pkt=false !
  )
else
  PIPELINE+=(
    videoconvert n-threads=8 ! "video/x-raw,format=I420" !
    x264enc "bitrate=$((BITRATE / 1000))" speed-preset=ultrafast tune=zerolatency
    key-int-max=15 bframes=0 byte-stream=true threads=8 sliced-threads=true !
  )
fi

PIPELINE+=(
  h264parse config-interval=-1 !
  mpegtsmux alignment=7 !
  udpsink host=127.0.0.1 "port=$UDP_PORT" buffer-size=4194304 sync=false async=false
  t. ! queue leaky=downstream max-size-buffers=1 !
  videorate drop-only=true "max-rate=$JPEG_FPS" !
  jpegenc quality=90 !
  multifilesink "location=$FRAME_FILE" max-files=1 sync=false
)

exec "${PIPELINE[@]}"
