#!/usr/bin/env bash
# Starts the AI Storyteller so a phone or tablet on your home network can use it too.
# Other devices must enter a PIN; this computer never does. Only use this on a network you trust.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/uvicorn ]; then echo "First-time setup not done. See README.md."; exit 1; fi
mkdir -p data
if [ ! -s data/lan_pin ]; then python3 -c "import secrets;print(''.join(secrets.choice('0123456789') for _ in range(6)))" > data/lan_pin; fi
export STORYTELLER_PIN="$(cat data/lan_pin)"
IP="$(hostname -I | awk '{print $1}')"
echo
echo "  On your phone or tablet open:  http://$IP:8000"
echo "  PIN: $STORYTELLER_PIN   (stored in data/lan_pin; delete that file to get a new one)"
echo
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
