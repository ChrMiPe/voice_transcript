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

# Eine stabile Identitaet macht das Designated Requirement rebuild-fest (siehe
# unten). Ist keine da, bleibt Ad-hoc — dann wechselt es bei jedem Build.
SIGN_IDENTITY="${VOICE_TRANSCRIPT_SIGN_IDENTITY:-}"
if [[ -n "$SIGN_IDENTITY" ]] && security find-identity -v -p codesigning \
        | grep -qF "$SIGN_IDENTITY"; then
    echo "==> Signieren (Identitaet: $SIGN_IDENTITY)..."
    codesign --force --deep --sign "$SIGN_IDENTITY" "$APP_PATH"
    STABLE_SIGNATURE=1
else
    echo "==> Signieren (ad-hoc)..."
    codesign --force --deep --sign - "$APP_PATH"
    STABLE_SIGNATURE=0
fi

# Das Bundle enthaelt MLX nicht (siehe build_app.spec/excludes) — der LLM-Server
# laeuft per `uv run` aus dem Repo. Pfad hinterlegen, damit die App ihn findet.
echo "==> Repo-Pfad hinterlegen..."
mkdir -p "$SUPPORT_DIR"
printf '%s\n' "$SCRIPT_DIR" > "$SUPPORT_DIR/project_dir"

# Ad-hoc-Signaturen haben als Designated Requirement einen nackten cdhash:
#
#     codesign -d -r- "$APP_PATH"
#     # designated => cdhash H"06bfbc80..."
#
# TCC speichert diese Anforderung zusammen mit der Berechtigung. Jeder Build
# erzeugt ein neues Binary und damit einen neuen cdhash — die gespeicherte
# Bedienungshilfen-Freigabe passt danach nicht mehr, obwohl der Schalter in den
# Systemeinstellungen weiter aktiviert *aussieht*. Genau diese Kombination kostet
# die meiste Zeit: ein Haken, der nichts tut.
#
# Deshalb den nun ungueltigen Eintrag aktiv wegraeumen. Der frueher hier
# stehende `sqlite3 INSERT` als Ersatz war immer ein No-op — die TCC-Datenbank
# ist SIP-geschuetzt und ausschliesslich fuer tccd beschreibbar.
#
# Mit einer stabilen Identitaet (VOICE_TRANSCRIPT_SIGN_IDENTITY, siehe oben) lautet
# das Requirement stattdessen `identifier "..." and certificate leaf = H"..."` und
# ueberlebt jeden Build — dann darf hier nichts zurueckgesetzt werden.
if [[ "$STABLE_SIGNATURE" == "1" ]]; then
    echo "==> Stabile Signatur — Berechtigungen bleiben erhalten."
    echo "==> Starten..."
    open "$APP_PATH"
    sleep 3
else
    echo "==> Ungueltige Bedienungshilfen-Freigabe aufraeumen..."
    tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1 || true

    echo "==> Starten..."
    open "$APP_PATH"
    sleep 3

    # Die App hat beim Start den TCC-Eintrag angelegt, steht also jetzt in der
    # Liste. Einen Dialog gibt es fuer Bedienungshilfen nicht mehr (tccd: "does
    # not allow prompting"), deshalb den Bereich direkt aufschlagen — der
    # Schalter ist der einzige verbleibende Handgriff.
    echo "==> Bedienungshilfen-Einstellungen oeffnen..."
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
fi

echo "==> Status..."
tail -5 "$SUPPORT_DIR/app.log" 2>/dev/null || true
echo ""
echo "   Der Hotkey laeuft ohne Berechtigung (Carbon, siehe hotkey.py)."
if [[ "$STABLE_SIGNATURE" != "1" ]]; then
    echo "   Fuer das Einfuegen am Cursor: \"Voice Transcript\" in der geoeffneten"
    echo "   Liste aus- und wieder einschalten (Ad-hoc-Signatur, neuer cdhash)."
fi
echo "   Log: $SUPPORT_DIR/app.log"

echo "==> Fertig!"
