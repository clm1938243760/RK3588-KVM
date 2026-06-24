#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
PID_FILE="${PID_FILE:-/tmp/rk3588_kvm.pid}"
LOG_FILE="${LOG_FILE:-/tmp/rk3588_kvm.log}"
STREAM_PID_FILE="${STREAM_PID_FILE:-/tmp/rk3588_kvm_stream.pid}"
STREAM_LOG_FILE="${STREAM_LOG_FILE:-/tmp/rk3588_kvm_stream.log}"
MEDIAMTX_PID_FILE="${MEDIAMTX_PID_FILE:-/tmp/rk3588_kvm_mediamtx.pid}"
MEDIAMTX_LOG_FILE="${MEDIAMTX_LOG_FILE:-/tmp/rk3588_kvm_mediamtx.log}"
FRAME_FILE="${FRAME_FILE:-/tmp/rk3588_kvm_latest.jpg}"
KVM_WIDTH="${KVM_WIDTH:-1920}"
KVM_HEIGHT="${KVM_HEIGHT:-1080}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo: sudo $ROOT/run.sh"
  exit 1
fi

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    echo "RK3588 KVM already running, pid=$PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

for dev in /dev/video40 /dev/hidg0 /dev/hidg1; do
  if [[ ! -e "$dev" ]]; then
    echo "Missing $dev"
    echo "If /dev/hidg0 or /dev/hidg1 is missing after reboot, run:"
    echo "  sudo systemctl start rk3588-usb-printer-gadget.service"
    exit 1
  fi
done

echo "KVM viewport: ${KVM_WIDTH}x${KVM_HEIGHT}"

KVM_ARGS=("$@")
if [[ -x /usr/local/bin/mediamtx && "${KVM_LEGACY_MJPEG:-0}" != "1" ]]; then
  sysctl -q -w net.core.rmem_max=4194304
  sysctl -q -w net.core.wmem_max=4194304
  nohup /usr/local/bin/mediamtx "$ROOT/mediamtx.yml" >"$MEDIAMTX_LOG_FILE" 2>&1 &
  echo "$!" > "$MEDIAMTX_PID_FILE"
  sleep 0.5
  : >"$STREAM_LOG_FILE"
  nohup "$ROOT/stream_watchdog.sh" >>"$STREAM_LOG_FILE" 2>&1 &
  echo "$!" > "$STREAM_PID_FILE"
  KVM_ARGS+=(--frame-file "$FRAME_FILE" --webrtc)
  echo "Hardware H.264 WebRTC enabled"
else
  echo "MediaMTX unavailable; using legacy MJPEG mode"
fi

nohup "$PYTHON" "$ROOT/rk3588_kvm.py" --width "$KVM_WIDTH" --height "$KVM_HEIGHT" "${KVM_ARGS[@]}" >"$LOG_FILE" 2>&1 &
echo "$!" > "$PID_FILE"
echo "RK3588 KVM started: pid=$(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
echo "Open: http://$(hostname -I | awk '{print $1}'):8090"
