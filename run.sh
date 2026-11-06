#!/usr/bin/env bash
# Starts the AI Storyteller and opens it at http://127.0.0.1:8000
cd "$(dirname "$0")"
if [ ! -x .venv/bin/uvicorn ]; then
  echo "First-time setup not done. See README.md (needs: sudo apt install python3.12-venv)."
  exit 1
fi
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 "$@"
