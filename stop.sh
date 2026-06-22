#!/bin/bash
set -euo pipefail

PID_FILE="${PID_FILE:-/tmp/rk3588_kvm.pid}"
STREAM_PID_FILE="${STREAM_PID_FILE:-/tmp/rk3588_kvm_stream.pid}"
MEDIAMTX_PID_FILE="${MEDIAMTX_PID_FILE:-/tmp/rk3588_kvm_mediamtx.pid}"
WORKER_PID_FILE="${WORKER_PID_FILE:-/tmp/rk3588_kvm_stream_worker.pid}"

for PID_PATH in "$PID_FILE" "$STREAM_PID_FILE" "$MEDIAMTX_PID_FILE"; do
  [[ -f "$PID_PATH" ]] || continue
  PID="$(cat "$PID_PATH")"
  if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 30); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f "$PID_PATH"
done
if [[ -f "$WORKER_PID_FILE" ]]; then
  WORKER_PID="$(cat "$WORKER_PID_FILE")"
  if [[ "$WORKER_PID" =~ ^[0-9]+$ ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
  fi
  rm -f "$WORKER_PID_FILE"
fi
echo "RK3588 KVM stopped."
