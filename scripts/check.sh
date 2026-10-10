#!/usr/bin/env bash
# Report which prerequisites are present. Never prints secret values.
# Exit code: 1 only if the app cannot start at all (no backend venv or no node).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BLOCKERS=0
HAVE_DOCKER=0
HAVE_IMAGE=0
HAVE_KEY=0
HAVE_DEMO=0

ok()   { printf '  [ok]      %-18s %s\n' "$1" "$2"; }
miss() { printf '  [missing] %-18s %s\n' "$1" "$2"; }
info() { printf '  [info]    %-18s %s\n' "$1" "$2"; }

# Value of KEY from the repo-root .env (last assignment wins), quotes stripped.
env_file_value() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1=[[:space:]]*//p" "$ENV_FILE" | tail -n1 | tr -d "\"'\r" | sed 's/[[:space:]]*$//'
}

echo "Explainer demo - environment check"
echo

# --- Python backend ----------------------------------------------------------------------
PY="$ROOT/backend/.venv/bin/python"
if [ -x "$PY" ]; then
  PYVER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
  if "$PY" -c 'import fastapi, uvicorn, anthropic, pydantic, dotenv' >/dev/null 2>&1; then
    ok "python venv" "backend/.venv (Python $PYVER, requirements installed)"
  else
    miss "python deps" "backend/.venv exists but requirements are missing - run: make setup-backend"
    BLOCKERS=$((BLOCKERS + 1))
  fi
else
  miss "python venv" "backend/.venv not found - run: make setup-backend"
  BLOCKERS=$((BLOCKERS + 1))
fi

# --- Node / frontend -------------------------------------------------------------------------
if command -v node >/dev/null 2>&1; then
  NODEVER="$(node --version)"
  NODEMAJOR="$(echo "$NODEVER" | sed 's/^v//' | cut -d. -f1)"
  if [ "${NODEMAJOR:-0}" -ge 20 ] 2>/dev/null; then
    ok "node" "$NODEVER"
  else
    miss "node" "$NODEVER is too old (Next.js needs >= 20.9)"
    BLOCKERS=$((BLOCKERS + 1))
  fi
  if [ -d "$ROOT/frontend/node_modules" ]; then
    ok "frontend deps" "frontend/node_modules present"
  elif [ -f "$ROOT/frontend/package.json" ]; then
    miss "frontend deps" "run: make setup-frontend"
  else
    miss "frontend" "frontend/package.json not found"
  fi
else
  miss "node" "not installed (https://nodejs.org, >= 20.9)"
  BLOCKERS=$((BLOCKERS + 1))
fi

# --- Docker sandbox ----------------------------------------------------------------------------
IMAGE="${RENDERER_IMAGE:-}"
[ -n "$IMAGE" ] || IMAGE="$(env_file_value RENDERER_IMAGE)"
[ -n "$IMAGE" ] || IMAGE="explainer-manim-renderer:0.19.2"

if ! command -v docker >/dev/null 2>&1; then
  miss "docker" "not installed - needed to render videos (Docker Desktop)"
elif docker info >/dev/null 2>&1; then
  HAVE_DOCKER=1
  ok "docker daemon" "running (server $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'))"
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    HAVE_IMAGE=1
    ok "renderer image" "$IMAGE"
  else
    miss "renderer image" "$IMAGE not built - run: make renderer"
  fi
else
  miss "docker daemon" "not running - start Docker Desktop"
fi
if [ "$HAVE_DOCKER" = "0" ]; then
  miss "renderer image" "$IMAGE (cannot check without the Docker daemon)"
fi

# --- Anthropic API key --------------------------------------------------------------------------
if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  HAVE_KEY=1
  ok "ANTHROPIC_API_KEY" "set in the environment"
elif [ -n "$(env_file_value ANTHROPIC_API_KEY)" ] || [ -n "$(env_file_value ANTHROPIC_AUTH_TOKEN)" ]; then
  HAVE_KEY=1
  ok "ANTHROPIC_API_KEY" "set in .env"
elif [ -f "$ENV_FILE" ]; then
  miss "ANTHROPIC_API_KEY" "empty in .env - live generation disabled"
else
  miss "ANTHROPIC_API_KEY" "no .env (cp .env.example .env, then fill in the key) - live generation disabled"
fi

# --- Offline demo fixture ------------------------------------------------------------------------
DEMO="$ROOT/backend/manim/offline_demo"
if [ -f "$DEMO/storyboard.json" ] && [ -f "$DEMO/scene.py" ]; then
  HAVE_DEMO=1
  ok "offline demo" "backend/manim/offline_demo/{storyboard.json,scene.py}"
else
  miss "offline demo" "fixture not found in backend/manim/offline_demo/"
fi

# --- Running servers (informational) -------------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
  if curl -fsS -m 2 "http://127.0.0.1:${BACKEND_PORT:-8000}/api/health" >/dev/null 2>&1; then
    info "backend" "running on :${BACKEND_PORT:-8000}"
  else
    info "backend" "not running (make dev)"
  fi
fi

echo
if [ "$BLOCKERS" -gt 0 ]; then
  echo "Not ready: fix the items above, then run 'make dev'."
  exit 1
fi
CAN_RENDER=$((HAVE_DOCKER * HAVE_IMAGE))
if [ "$CAN_RENDER" = "1" ] && [ "$HAVE_KEY" = "1" ]; then
  echo "Ready: live AI generation (make dev)."
elif [ "$CAN_RENDER" = "1" ] && [ "$HAVE_DEMO" = "1" ]; then
  echo "Ready for the offline demo only (no API key). Live generation needs ANTHROPIC_API_KEY."
elif [ "$CAN_RENDER" = "1" ]; then
  echo "Rendering works, but there is no API key and no offline demo fixture."
else
  echo "The app will start, but videos cannot be rendered until Docker and the renderer image are ready."
fi
exit 0
