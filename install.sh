#!/bin/bash
set -e

# ─── Voice Transcript — Ein-Klick-Installer ───
# Installiert alle Voraussetzungen und baut die App.
# Nutzung:  bash install.sh

APP_NAME="Voice Transcript"
BUNDLE_ID="com.voicetranscript.app"
APP_PATH="/Applications/${APP_NAME}.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()  { echo -e "\n${GREEN}==> $1${NC}"; }

# ─── Voraussetzungen pruefen ───

step "System pruefen..."

# macOS?
[[ "$(uname)" == "Darwin" ]] || error "Dieses Tool laeuft nur auf macOS."

# Apple Silicon?
if [[ "$(uname -m)" != "arm64" ]]; then
    error "Apple Silicon (M1/M2/M3/M4) wird benoetigt. Intel Macs werden nicht unterstuetzt."
fi

info "macOS auf Apple Silicon erkannt"

# ─── Homebrew ───

step "Homebrew pruefen..."

if command -v brew &>/dev/null; then
    info "Homebrew ist installiert"
else
    warn "Homebrew nicht gefunden — wird installiert..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv)"
    info "Homebrew installiert"
fi

# ─── yap (Spracherkennung) ───

step "yap pruefen..."

if command -v yap &>/dev/null || [[ -f /opt/homebrew/bin/yap ]]; then
    info "yap ist installiert"
else
    warn "yap nicht gefunden — wird installiert..."
    brew install yap
    info "yap installiert"
fi

# ─── uv (Python-Paketmanager) ───

step "uv pruefen..."

if command -v uv &>/dev/null; then
    info "uv ist installiert ($(uv --version))"
else
    warn "uv nicht gefunden — wird installiert..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    info "uv installiert ($(uv --version))"
fi

# ─── Python-Dependencies ───

step "Python-Dependencies installieren..."

cd "$SCRIPT_DIR"
uv sync
info "Dependencies installiert"

# ─── LLM-Modell herunterladen ───

step "LLM-Modell pruefen..."

MODEL_DIR="$HOME/.cache/huggingface/hub/models--mlx-community--Qwen3-4B-4bit"

if [[ -d "$MODEL_DIR" ]]; then
    info "Modell bereits vorhanden"
else
    warn "Modell wird heruntergeladen (~2.1 GB) — das dauert beim ersten Mal..."
    uv run python -c "from mlx_lm import load; load('mlx-community/Qwen3-4B-4bit')"
    info "Modell heruntergeladen"
fi

# ─── App bauen ───

step "App bauen..."

# Laufende Instanz stoppen
pkill -f "${APP_PATH}/Contents/MacOS/" 2>/dev/null || true
sleep 1

uv run pyinstaller build_app.spec --noconfirm 2>&1 | tail -5

# Installieren
rm -rf "$APP_PATH"
cp -R "dist/${APP_NAME}.app" "$APP_PATH"

# Ad-hoc signieren. Ein Requirement auf die Bundle-ID zu setzen wuerde die
# Bedienungshilfen-Freigabe *nicht* rebuild-fest machen — TCC prueft trotzdem einen
# cdhash (gemessen, siehe build.sh) — und waere eine Aufweichung.
codesign --force --deep --sign - "$APP_PATH"

# Das Bundle enthaelt MLX nicht (siehe build_app.spec/excludes) — der LLM-Server
# laeuft per `uv run` aus dem Repo. Pfad hinterlegen, damit die App ihn findet.
SUPPORT_DIR="$HOME/Library/Application Support/VoiceTranscript"
mkdir -p "$SUPPORT_DIR"
printf '%s\n' "$SCRIPT_DIR" > "$SUPPORT_DIR/project_dir"
info "App gebaut und installiert"

# ─── Berechtigungen ───

step "Berechtigungen..."

# Kein Schreiben in die TCC.db: die ist SIP-geschuetzt und ausschliesslich fuer
# tccd beschreibbar — der frueher hier stehende INSERT war immer ein No-op.
# Mikrofon und Spracherkennung fragt macOS beim ersten Diktat selbst ab.
# Eingabeueberwachung braucht die App nicht mehr (Carbon-Hotkey, siehe hotkey.py).
info "Mikrofon-Zugriff wird beim ersten Diktat abgefragt"
warn "Bedienungshilfen (nur fuer das Einfuegen am Cursor) erteilst du ueber den"
warn "Menueeintrag \"Bedienungshilfen fehlen\" — er oeffnet den richtigen Dialog"

# ─── Autostart einrichten ───

step "Autostart einrichten..."

LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LAUNCH_AGENT="$LAUNCH_AGENT_DIR/com.voicetranscript.app.plist"
mkdir -p "$LAUNCH_AGENT_DIR"
cp "$SCRIPT_DIR/com.voicetranscript.app.plist" "$LAUNCH_AGENT"
# Alten LaunchAgent entladen falls vorhanden
launchctl bootout "gui/$(id -u)/com.voicetranscript.app" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT"
info "Autostart eingerichtet (LaunchAgent)"

# ─── Starten ───

step "App starten..."
open "$APP_PATH"

# ─── Zusammenfassung ───

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN} Voice Transcript wurde erfolgreich installiert!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Der Hotkey (Ctrl+Cmd+E) braucht keine Berechtigung."
echo "Meldet das Menue, die Kombination sei belegt: im Menue"
echo "'Hotkey aendern...' eine andere waehlen."
echo ""
echo "Landet der Text nur im Clipboard, fehlen die Bedienungshilfen:"
echo "  Menueleiste > '⚠ Bedienungshilfen fehlen' anklicken"
echo "  → Systemeinstellungen oeffnen, 'Voice Transcript' aktivieren"
echo ""
echo "Die App laeuft in der Menueleiste (Mikrofon-Icon)."
echo "Log bei Problemen: ~/Library/Application Support/VoiceTranscript/app.log"
echo ""
