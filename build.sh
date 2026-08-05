#!/bin/bash
set -e

APP_NAME="Voice Transcript"
BUNDLE_ID="com.voicetranscript.app"
APP_PATH="/Applications/${APP_NAME}.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/VoiceTranscript"

echo "==> App stoppen..."
pkill -f "Voice Transcript" 2>/dev/null || true
sleep 1

echo "==> Bauen..."
uv run pyinstaller build_app.spec --noconfirm 2>&1 | tail -3

echo "==> Installieren..."
rm -rf "$APP_PATH"
cp -R "dist/${APP_NAME}.app" "$APP_PATH"

echo "==> Signieren..."
codesign --force --deep --sign - "$APP_PATH"

# Das Bundle enthaelt MLX nicht (siehe build_app.spec/excludes) — der LLM-Server
# laeuft per `uv run` aus dem Repo. Pfad hinterlegen, damit die App ihn findet.
echo "==> Repo-Pfad hinterlegen..."
mkdir -p "$SUPPORT_DIR"
printf '%s\n' "$SCRIPT_DIR" > "$SUPPORT_DIR/project_dir"

# Frueher stand hier `tccutil reset Accessibility` plus ein direkter INSERT in
# die TCC.db. Der INSERT kann nicht funktionieren — die Datenbank ist von SIP
# geschuetzt und nur fuer tccd schreibbar. Das Reset lief dagegen sehr wohl und
# hat die einmal erteilten Bedienungshilfen bei *jedem* Build wieder entfernt.
# Ergebnis: das Einfuegen am Cursor war nach jedem Rebuild kaputt.
#
# Die Bundle-ID bleibt stabil, deshalb ueberlebt die Berechtigung einen Rebuild,
# solange man sie nicht aktiv zuruecksetzt. Der Hotkey braucht seit dem Umstieg
# auf Carbon (siehe hotkey.py) ueberhaupt keine Berechtigung mehr.

echo "==> Starten..."
open "$APP_PATH"
sleep 2

echo "==> Status..."
tail -5 "$SUPPORT_DIR/app.log" 2>/dev/null || true
echo "   Log: $SUPPORT_DIR/app.log"
echo "   Meldet das Menue \"Bedienungshilfen fehlen\", den Eintrag anklicken —"
echo "   das ist nur fuer das Einfuegen am Cursor noetig, nicht fuer den Hotkey."

echo "==> Fertig!"
