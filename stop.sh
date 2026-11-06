#!/usr/bin/env bash
# Stops the AI Storyteller server (the part that runs in the background). Your stories are saved continuously, so nothing is lost.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
say() { command -v notify-send >/dev/null 2>&1 && notify-send -i "$PWD/static/icon.png" "AI Storyteller" "$1"; echo "$1"; }
pid=$(ss -ltnp 'sport = :8000' 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
if [ -z "$pid" ]; then say "AI Storyteller is not running."; exit 0; fi
if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "app.main"; then
  kill "$pid" && say "AI Storyteller stopped."
else
  say "Port 8000 is used by something else, so I left it alone."
fi
