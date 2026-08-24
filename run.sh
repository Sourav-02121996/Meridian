#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PYTHON="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Meridian: missing .venv. Run: python3.11 -m venv .venv && .venv/bin/python -m pip install -r backend/requirements.txt"
  exit 1
fi

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd backend && "$PYTHON" -m uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!
(cd frontend && npm run dev -- --host 0.0.0.0) &
FRONTEND_PID=$!
wait
