#!/usr/bin/env bash
# AI Storyteller installer for Linux (Ubuntu, Debian, Fedora, and similar).
# Sets up the app in this folder, and adds a desktop icon. Safe to run again.
#
#   ./install.sh               ask before optional steps
#   ./install.sh --yes         say yes to the optional steps (Ollama for local models)
#   ./install.sh --no-desktop  do not create the desktop icon / menu entry
set -e
cd "$(dirname "$(readlink -f "$0")")"
HERE="$PWD"
YES=0; DESKTOP=1
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --no-desktop) DESKTOP=0 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
  esac
done

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
ask()  { # ask "question" -> returns 0 for yes
  if [ "$YES" = 1 ]; then return 0; fi
  if [ ! -t 0 ]; then return 1; fi
  read -r -p "$1 [y/N] " ans; [[ "$ans" =~ ^[Yy] ]]
}

say "1/4  Checking Python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it first (Ubuntu/Debian: sudo apt install python3 python3-venv), then run this again."
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer is needed (you have $(python3 --version 2>&1))."
  exit 1
fi
if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
  echo "The Python 'venv' package is missing."
  if command -v apt >/dev/null 2>&1; then
    if ask "Install it now with 'sudo apt install python3-venv'?"; then sudo apt install -y python3-venv
    else echo "Run: sudo apt install python3-venv   and then ./install.sh again."; exit 1; fi
  else
    echo "Install your system's python3-venv package, then run this again."; exit 1
  fi
fi

say "2/4  Installing the app's libraries (this downloads a few small packages)"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
chmod +x run.sh run-lan.sh launch.sh stop.sh install.sh uninstall.sh 2>/dev/null || true
mkdir -p data
echo "Done."

say "3/4  Desktop icon"
if [ "$DESKTOP" = 1 ] && [ "$(uname -s)" = "Linux" ]; then
  BIN="$HOME/.local/bin"; APPS="$HOME/.local/share/applications"
  mkdir -p "$BIN" "$APPS"
  printf '#!/usr/bin/env bash\nexec "%s/launch.sh" "$@"\n' "$HERE" > "$BIN/ai-storyteller"
  printf '#!/usr/bin/env bash\nexec "%s/stop.sh" "$@"\n' "$HERE" > "$BIN/ai-storyteller-stop"
  chmod +x "$BIN/ai-storyteller" "$BIN/ai-storyteller-stop"
  cat > "$APPS/ai-storyteller.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AI Storyteller
Comment=Play AI-narrated interactive stories (starts the app and opens it in your browser)
Exec=$BIN/ai-storyteller
Icon=$HERE/static/icon.png
Terminal=false
Categories=Game;
StartupNotify=false
Actions=Stop;

[Desktop Action Stop]
Name=Stop AI Storyteller
Exec=$BIN/ai-storyteller-stop
EOF
  chmod +x "$APPS/ai-storyteller.desktop"
  DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  if [ -d "$DESK" ]; then
    cp "$APPS/ai-storyteller.desktop" "$DESK/AI Storyteller.desktop"
    chmod +x "$DESK/AI Storyteller.desktop"
    command -v gio >/dev/null 2>&1 && gio set "$DESK/AI Storyteller.desktop" metadata::trusted true >/dev/null 2>&1 || true
  fi
  echo "Added 'AI Storyteller' to your application menu and Desktop. (If the Desktop icon asks, right-click it and choose Allow Launching.)"
else
  echo "Skipped. Start the app with ./launch.sh"
fi

say "4/4  Local AI models (optional)"
echo "The app works with online AIs (Claude, ChatGPT, Gemini, Grok: you need an API key) and/or free local models through Ollama."
if command -v ollama >/dev/null 2>&1; then
  echo "Ollama is already installed."
elif ask "Install Ollama (free local models; downloads from ollama.com and needs sudo)?"; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "Skipped. You can install it later from https://ollama.com if you want local models."
fi

say "All set."
echo "Start the app:   double-click 'AI Storyteller' on your Desktop, or run  ./launch.sh"
echo "Then open Settings (the gear) and choose your AI. The README explains the first settings to check."
