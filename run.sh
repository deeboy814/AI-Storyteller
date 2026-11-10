#!/usr/bin/env bash
# Starts the AI Storyteller and opens it at http://127.0.0.1:8000
cd "$(dirname "$0")"
if [ ! -x .venv/bin/uvicorn ]; then
  echo "First-time setup not done. See README.md (needs: sudo apt install python3.12-venv)."
  exit 1
fi
# Settings → Phone & tablet: when it is on, other devices on the home network can open the app (with a PIN); otherwise only this computer can.
HOST=127.0.0.1
if .venv/bin/python -c "import json,sys; sys.exit(0 if json.load(open('data/config.json')).get('lan_access') else 1)" 2>/dev/null; then
  HOST=0.0.0.0
  [ -s data/lan_pin ] || python3 -c "import secrets;print(''.join(secrets.choice('0123456789') for _ in range(6)))" > data/lan_pin
  export STORYTELLER_PIN="$(cat data/lan_pin)" STORYTELLER_LAN=1
fi
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port 8000 "$@"
