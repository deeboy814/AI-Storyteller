#!/usr/bin/env bash
# One-click start: starts the AI Storyteller server if it is not already running, then opens it in your browser.
# (This is what the desktop launcher runs. You can also run it from a terminal.)
cd "$(dirname "$(readlink -f "$0")")" || exit 1
URL="http://127.0.0.1:8000"
say() { command -v notify-send >/dev/null 2>&1 && notify-send -i "$PWD/static/icon.png" "AI Storyteller" "$1"; echo "$1"; }
up() { curl -s -o /dev/null --max-time 1 "$URL/api/stories"; }

if ! up; then
  if [ ! -x .venv/bin/uvicorn ]; then
    say "First-time setup is not done. See README.md."
    exit 1
  fi
  mkdir -p data
  say "Starting AI Storyteller…"
  nohup ./run.sh >> data/server.log 2>&1 &
  for _ in $(seq 1 60); do
    up && break
    sleep 0.5
  done
  if ! up; then
    say "It did not start. Details are in data/server.log"
    exit 1
  fi
fi
xdg-open "$URL" >/dev/null 2>&1 || echo "Open $URL in your browser."
