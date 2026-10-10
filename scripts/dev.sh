#!/usr/bin/env bash
# Start the backend (FastAPI on :8000) and the frontend (Next.js on :3000) together.
# Ctrl-C stops both. If either one exits, the other is stopped too.
#
#   BACKEND_PORT=8000 FRONTEND_PORT=3000 RELOAD=1 scripts/dev.sh
#
# Written for the bash 3.2 that ships with macOS (no `wait -n`, no arrays under set -u).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:${BACKEND_PORT}}"
UVICORN="$ROOT/backend/.venv/bin/uvicorn"

say() { printf 'dev: %s\n' "$*"; }
die() { printf 'dev: %s\n' "$*" >&2; exit 1; }
port_busy() { command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

[ -x "$UVICORN" ] || die "backend/.venv is missing - run 'make setup' first."
port_busy "$BACKEND_PORT" && die "port $BACKEND_PORT is already in use (is another backend running?)."

RUN_FRONTEND=1
if [ ! -f "$ROOT/frontend/package.json" ]; then
  say "frontend/package.json not found - starting the backend only."
  RUN_FRONTEND=0
elif [ ! -d "$ROOT/frontend/node_modules" ]; then
  die "frontend dependencies are missing - run 'make setup' (or: cd frontend && npm install)."
elif port_busy "$FRONTEND_PORT"; then
  die "port $FRONTEND_PORT is already in use (is another frontend running?)."
fi

# Job control: every background job gets its own process group, so stopping a
# group also stops its children (npm -> next -> node workers, uvicorn workers).
set -m

BACKEND_PID=""
FRONTEND_PID=""

alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

stop_all() {
  trap - INT TERM EXIT
  exec 2>/dev/null   # hide bash's own "Terminated" job notices; children keep their stderr
  say "stopping..."
  for pid in $FRONTEND_PID $BACKEND_PID; do
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  done
  # Up to ~8 s for a graceful exit, then force.
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
    if ! alive "$FRONTEND_PID" && ! alive "$BACKEND_PID"; then break; fi
    sleep 0.5
  done
  for pid in $FRONTEND_PID $BACKEND_PID; do
    if alive "$pid"; then
      say "force-stopping process group $pid"
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    fi
  done
  wait 2>/dev/null
  say "stopped."
}
trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM
trap 'stop_all' EXIT

RELOAD_ARGS=""
if [ "${RELOAD:-0}" = "1" ]; then
  RELOAD_ARGS="--reload --reload-dir app"   # only app/: job dirs contain .py files too
fi

(cd "$ROOT/backend" && exec "$UVICORN" app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" $RELOAD_ARGS) &
BACKEND_PID=$!

if [ "$RUN_FRONTEND" = "1" ]; then
  (cd "$ROOT/frontend" && exec npm run dev -- --port "$FRONTEND_PORT") &
  FRONTEND_PID=$!
fi

say "backend  -> http://127.0.0.1:${BACKEND_PORT}  (health: /api/health)"
[ "$RUN_FRONTEND" = "1" ] && say "frontend -> http://localhost:${FRONTEND_PORT}  (proxies /api/* to ${BACKEND_URL})"
say "press Ctrl-C to stop both."

while :; do
  if ! alive "$BACKEND_PID"; then say "backend exited."; exit 1; fi
  if [ "$RUN_FRONTEND" = "1" ] && ! alive "$FRONTEND_PID"; then say "frontend exited."; exit 1; fi
  sleep 1
done
