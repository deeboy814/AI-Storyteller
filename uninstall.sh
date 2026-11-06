#!/usr/bin/env bash
# Removes the desktop icon and menu entry made by install.sh. Your stories and settings (the data folder) are NOT touched.
# To remove the app completely, also delete this folder (after backing up data/ if you want to keep your stories).
rm -f "$HOME/.local/share/applications/ai-storyteller.desktop" "$HOME/.local/bin/ai-storyteller" "$HOME/.local/bin/ai-storyteller-stop"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
rm -f "$DESK/AI Storyteller.desktop"
echo "Removed the AI Storyteller icon and menu entry. Your data folder was left alone."
