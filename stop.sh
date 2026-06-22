#!/bin/bash
set -euo pipefail

PID_FILE="${PID_FILE:-/tmp/rk3588_kvm.pid}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "RK3588 KVM is not running."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 30); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.1
  done
fi
rm -f "$PID_FILE"
echo "RK3588 KVM stopped."
