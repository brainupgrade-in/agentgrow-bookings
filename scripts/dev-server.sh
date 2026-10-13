#!/usr/bin/env bash
# Dev webhook server on :8080 (reachable via https://bookings.agentgrow.io). Usage: scripts/dev-server.sh start|stop|restart|status
set -euo pipefail
cd "$(dirname "$0")/.."
PIDFILE=data/server.pid LOG=data/server.log
mkdir -p data

stop() { [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; }
start() {
  nohup .venv/bin/uvicorn bookings.app:default_app --factory --host 0.0.0.0 --port 8080 >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  for _ in $(seq 1 20); do curl -sf -o /dev/null "http://127.0.0.1:8080/demo" && { echo "up (pid $(cat "$PIDFILE"))"; return; }; sleep 0.5; done
  echo "did not start; see $LOG" >&2; exit 1
}
case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo "up (pid $(cat "$PIDFILE"))" || echo down ;;
esac
