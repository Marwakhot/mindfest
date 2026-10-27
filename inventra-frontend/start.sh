#!/usr/bin/env bash
# Start the Inventra backend (unchanged) and this frontend together. Ctrl+C stops both.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="${BACKEND_DIR:-$HERE/../inventra-backend}"
( cd "$BACKEND" && .venv/bin/python -m uvicorn inventra.api.main:app --host 127.0.0.1 --port 8000 ) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT INT TERM
python3 "$HERE/serve.py" 3000
