#!/bin/bash
set -euo pipefail

VIDEO_DEVICE="${VIDEO_DEVICE:-/dev/video40}"
RTP_PORT="${RTP_PORT:-5004}"
TARGET_BITRATE="${TARGET_BITRATE:-12000000}"
MIN_BITRATE="${MIN_BITRATE:-500000}"
MAX_BITRATE="${MAX_BITRATE:-24000000}"
GOP_SIZE="${GOP_SIZE:-60}"
QP_MIN="${QP_MIN:-16}"
QP_MAX="${QP_MAX:-32}"
QP_INIT="${QP_INIT:-24}"
MAX_REENC="${MAX_REENC:-1}"
ENCODER="${ENCODER:-mpp}"

normalize_stream_fps() {
  local fps="${1:-0}"
  awk -v fps="$fps" 'BEGIN {
    f = fps + 0
    if (f >= 55) print 60
    else if (f >= 48) print 50
    else if (f >= 29) print 30
    else if (f >= 24) print 25
    else print 30
  }'
}

detect_source_fps() {
  local info fps
  info="$(v4l2-ctl -d "$VIDEO_DEVICE" --all 2>/dev/null || true)"
  fps="$(
    printf '%s\n' "$info" |
      sed -n \
        -e 's/.*(\([0-9.][0-9.]*\) frames per second).*/\1/p' \
        -e 's/.*Frames per second:[[:space:]]*\([0-9.][0-9.]*\).*/\1/p' \
        -e 's/.*fps:[[:space:]]*\([0-9.][0-9.]*\).*/\1/p' |
      head -1
  )"
  printf '%s' "$fps"
}

SOURCE_FPS="${SOURCE_FPS:-$(detect_source_fps)}"
STREAM_FPS="$(normalize_stream_fps "${STREAM_FPS:-$SOURCE_FPS}")"
echo "KVM WebRTC stream: source ${SOURCE_FPS:-unknown}fps -> 1920x1080 ${STREAM_FPS}fps max, VBR target ${TARGET_BITRATE}bps peak ${MAX_BITRATE}bps, GOP ${GOP_SIZE}, QP ${QP_MIN}-${QP_MAX}, re-encode ${MAX_REENC}"

PIPELINE=(
  gst-launch-1.0 -q -e
  v4l2src "device=$VIDEO_DEVICE" io-mode=2 do-timestamp=true !
  "video/x-raw,format=BGR,interlace-mode=progressive" !
)

PIPELINE+=(queue leaky=downstream max-size-buffers=1 max-size-time=0 max-size-bytes=0 !)
if [[ "$ENCODER" == "mpp" ]]; then
  PIPELINE+=(
    videorate drop-only=true "max-rate=$STREAM_FPS" ! videoscale method=0 !
    "video/x-raw,format=BGR,width=1920,height=1080,framerate=$STREAM_FPS/1,interlace-mode=progressive" !
    mpph264enc "bps=$TARGET_BITRATE" "bps-min=$MIN_BITRATE" "bps-max=$MAX_BITRATE"
    "gop=$GOP_SIZE" profile=100 level=42 header-mode=1 rc-mode=0 "max-reenc=$MAX_REENC"
    "qp-init=$QP_INIT" "qp-min=$QP_MIN" "qp-max=$QP_MAX" "qp-min-i=$QP_MIN" "qp-max-i=$QP_MAX"
    qos=true zero-copy-pkt=false !
  )
else
  PIPELINE+=(
    videorate drop-only=true "max-rate=$STREAM_FPS" ! videoconvert n-threads=8 ! videoscale method=0 !
    "video/x-raw,format=I420,width=1920,height=1080,framerate=$STREAM_FPS/1" !
    x264enc "bitrate=$((TARGET_BITRATE / 1000))" speed-preset=ultrafast tune=zerolatency
    "key-int-max=$GOP_SIZE" "qp-min=$QP_MIN" "qp-max=$QP_MAX"
    "option-string=vbv-maxrate=$((MAX_BITRATE / 1000)):vbv-bufsize=$((MAX_BITRATE / 1000))"
    bframes=0 byte-stream=true threads=8 sliced-threads=true !
  )
fi

PIPELINE+=(
  h264parse config-interval=-1 !
  rtph264pay pt=96 config-interval=-1 aggregate-mode=zero-latency mtu=1200 !
  udpsink host=127.0.0.1 "port=$RTP_PORT" buffer-size=4194304 sync=false async=false
)

exec "${PIPELINE[@]}"
