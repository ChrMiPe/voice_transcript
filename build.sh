#!/bin/bash
set -e

APP_NAME="Voice Transcript"
BUNDLE_ID="com.voicetranscript.app"
APP_PATH="/Applications/${APP_NAME}.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/VoiceTranscript"

echo "==> App stoppen..."
# Eng auf das Bundle gemuenzt. `pkill -f "Voice Transcript"` traf jede
# Kommandozeile, die den String enthielt — auch einen Editor mit einer Datei
# dieses Namens oder das eigene grep.
pkill -f "${APP_PATH}/Contents/MacOS/" 2>/dev/null || true

# Den LLM-Server ausdruecklich mit. Er laeuft als eigener `uv run`-Prozess und
# ueberlebt das enge pkill — dann bedient nach dem Rebuild weiter *alter* Code die
# Anfragen. Genau daran ist einmal eine halbe Fehlersuche haengengeblieben: der
# Server kannte die neue Transkriptions-Anfrage nicht und antwortete mit "ok" und
# leerem Text. Die App startet ihn beim Hochfahren neu.
pkill -f "voice_transcript.llm_server" 2>/dev/null || true
rm -f /tmp/voice_transcript_llm.sock /tmp/voice_transcript_llm.pid
sleep 1

echo "==> Bauen..."
uv run pyinstaller build_app.spec --noconfirm 2>&1 | tail -3

echo "==> Installieren..."
rm -rf "$APP_PATH"
cp -R "dist/${APP_NAME}.app" "$APP_PATH"

# ─── Signieren ───
#
# Ad-hoc. Ein Versuch, das Designated Requirement auf die Bundle-ID zu setzen, ist
# widerlegt: TCC prueft trotzdem einen cdhash. Gemessen am laufenden System, bei
# einem Bundle dessen DR ausdruecklich `identifier "com.voicetranscript.app"` war:
#
#     tccd: matchesCodeRequirement: ... from com.voicetranscript.app
#           : cdhash H"d7541629..."; status: -67050      (errSecCSReqFailed)
#
# Dass `codesign` das DR stabil setzt, war nachweisbar — dass TCC es benutzt, nicht.
# Und es waere eine echte Aufweichung: mehrere Bundles auf einer Platte koennen
# dieselbe Bundle-ID tragen (hier drei), jedes haette die Freigabe geerbt.
#
# Wer die Freigabe wirklich rebuild-fest will, braucht ein echtes Zertifikat
# (VOICE_TRANSCRIPT_SIGN_IDENTITY) — dann lautet das Requirement
# `identifier "..." and certificate leaf = H"..."` und ist an den Schluessel
# gebunden statt an das Binary.
SIGN_IDENTITY="${VOICE_TRANSCRIPT_SIGN_IDENTITY:-}"
if [[ -n "$SIGN_IDENTITY" ]] && security find-identity -v -p codesigning \
        | grep -qF "$SIGN_IDENTITY"; then
    echo "==> Signieren (Zertifikat: $SIGN_IDENTITY)..."
    codesign --force --deep --sign "$SIGN_IDENTITY" "$APP_PATH"
    STABILE_SIGNATUR=1
else
    echo "==> Signieren (ad-hoc)..."
    codesign --force --deep --sign - "$APP_PATH"
    STABILE_SIGNATUR=0
fi

echo "==> Signatur pruefen..."
codesign --verify --deep --strict "$APP_PATH" && echo "   gueltig"
codesign -d -r- "$APP_PATH" 2>/dev/null | grep designated | sed 's/^/   /'

# Das Bundle enthaelt MLX nicht (siehe build_app.spec/excludes) — der LLM-Server
# laeuft per `uv run` aus dem Repo. Pfad hinterlegen, damit die App ihn findet.
echo "==> Repo-Pfad hinterlegen..."
mkdir -p "$SUPPORT_DIR"
printf '%s\n' "$SCRIPT_DIR" > "$SUPPORT_DIR/project_dir"

# Kein `tccutil reset`: gemessen ersetzt es die gespeicherte Anforderung *nicht*
# (derselbe cdhash vor und nach dem Reset), zerstoert aber eine eventuell noch
# funktionierende Freigabe. Dasselbe gilt fuers Umlegen des Schalters.
echo "==> Starten..."
open "$APP_PATH"
sleep 3

echo "==> Status..."
tail -5 "$SUPPORT_DIR/app.log" 2>/dev/null || true
echo ""
echo "   Der Hotkey laeuft ohne Berechtigung (Carbon, siehe hotkey.py)."
if [[ "$STABILE_SIGNATUR" != "1" ]]; then
    echo ""
    echo "   Ad-hoc signiert wechselt der cdhash bei jedem Build, und TCC bindet die"
    echo "   Bedienungshilfen-Freigabe daran. Meldet das Menue \"Bedienungshilfen"
    echo "   fehlen\", obwohl der Schalter an ist:"
    echo ""
    echo "     Systemeinstellungen > Datenschutz & Sicherheit > Bedienungshilfen"
    echo "     Eintrag markieren, mit \"−\" ENTFERNEN, mit \"+\" neu hinzufuegen."
    echo ""
    echo "   Nur das Entfernen der Zeile bringt TCC dazu, die Anforderung neu zu"
    echo "   erfassen — Aus- und Einschalten allein reicht nicht."
fi
echo "==> Fertig!"
