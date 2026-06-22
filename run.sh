#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
PID_FILE="${PID_FILE:-/tmp/rk3588_kvm.pid}"
LOG_FILE="${LOG_FILE:-/tmp/rk3588_kvm.log}"

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

if systemctl is-active --quiet lightdm 2>/dev/null; then
  echo "Stopping lightdm so HDMI OUT can be driven by KMS mirror"
  systemctl stop lightdm || true
  sleep 1
fi

nohup "$PYTHON" "$ROOT/rk3588_kvm.py" "$@" >"$LOG_FILE" 2>&1 &
echo "$!" > "$PID_FILE"
echo "RK3588 KVM started: pid=$(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
echo "Open: http://$(hostname -I | awk '{print $1}'):8090"
