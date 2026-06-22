#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STREAM_LOG_FILE="${STREAM_LOG_FILE:-/tmp/rk3588_kvm_stream.log}"
WORKER_PID_FILE="${WORKER_PID_FILE:-/tmp/rk3588_kvm_stream_worker.pid}"
API_URL="${MEDIAMTX_PATH_API:-http://127.0.0.1:9997/v3/paths/get/kvm}"
CHECK_SECONDS="${STREAM_CHECK_SECONDS:-5}"
GRACE_SECONDS="${STREAM_GRACE_SECONDS:-8}"
MAX_FAILURES="${STREAM_MAX_FAILURES:-2}"

WORKER_PID=""

cleanup() {
  if [[ "$WORKER_PID" =~ ^[0-9]+$ ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  rm -f "$WORKER_PID_FILE"
}
trap cleanup EXIT
trap 'exit 0' INT TERM

while true; do
  echo "$(date -Is) starting video stream" >>"$STREAM_LOG_FILE"
  "$ROOT/stream_mpp.sh" >>"$STREAM_LOG_FILE" 2>&1 &
  WORKER_PID="$!"
  echo "$WORKER_PID" >"$WORKER_PID_FILE"
  sleep "$GRACE_SECONDS"

  FAILURES=0
  LAST_BYTES=""
  while kill -0 "$WORKER_PID" 2>/dev/null; do
    STATUS="$(curl -fsS --max-time 2 "$API_URL" 2>/dev/null || true)"
    CURRENT_BYTES="$(printf '%s' "$STATUS" | sed -n 's/.*"inboundBytes":\([0-9][0-9]*\).*/\1/p')"
    if [[ "$STATUS" == *'"ready":true'* && -n "$CURRENT_BYTES" && "$CURRENT_BYTES" != "$LAST_BYTES" ]]; then
      FAILURES=0
      LAST_BYTES="$CURRENT_BYTES"
    else
      FAILURES=$((FAILURES + 1))
      echo "$(date -Is) stream health failure $FAILURES/$MAX_FAILURES bytes=${CURRENT_BYTES:-unknown}" >>"$STREAM_LOG_FILE"
    fi
    if (( FAILURES >= MAX_FAILURES )); then
      echo "$(date -Is) stream stalled; restarting worker pid=$WORKER_PID" >>"$STREAM_LOG_FILE"
      kill "$WORKER_PID" 2>/dev/null || true
      break
    fi
    sleep "$CHECK_SECONDS"
  done

  wait "$WORKER_PID" 2>/dev/null || true
  rm -f "$WORKER_PID_FILE"
  WORKER_PID=""
  sleep 1
done
