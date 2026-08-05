#!/bin/bash
set -e

APP_NAME="Voice Transcript"
BUNDLE_ID="com.voicetranscript.app"
APP_PATH="/Applications/${APP_NAME}.app"

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

echo "==> Berechtigungen zuruecksetzen..."
tccutil reset Accessibility "$BUNDLE_ID" 2>/dev/null
tccutil reset ListenEvent "$BUNDLE_ID" 2>/dev/null

echo "==> Berechtigungen setzen..."
# Bedienungshilfen + Eingabeueberwachung automatisch eintragen
BINARY_PATH="$APP_PATH/Contents/MacOS/${APP_NAME}"
sqlite3 "$HOME/Library/Application Support/com.apple.TCC/TCC.db" \
  "INSERT OR REPLACE INTO access (service, client, client_type, auth_value, auth_reason, auth_version, indirect_object_identifier_type, indirect_object_identifier, flags, last_modified) VALUES ('kTCCServiceAccessibility', '$BUNDLE_ID', 0, 2, 0, 1, 0, 'UNUSED', 0, strftime('%s','now'));" 2>/dev/null || echo "   (Bedienungshilfen manuell hinzufuegen)"

sqlite3 "$HOME/Library/Application Support/com.apple.TCC/TCC.db" \
  "INSERT OR REPLACE INTO access (service, client, client_type, auth_value, auth_reason, auth_version, indirect_object_identifier_type, indirect_object_identifier, flags, last_modified) VALUES ('kTCCServiceListenEvent', '$BUNDLE_ID', 0, 2, 0, 1, 0, 'UNUSED', 0, strftime('%s','now'));" 2>/dev/null || echo "   (Eingabeueberwachung manuell hinzufuegen)"

echo "==> Starten..."
open "$APP_PATH"

echo "==> Fertig!"
