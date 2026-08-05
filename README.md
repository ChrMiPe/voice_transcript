# Voice Transcript

Eine leichtgewichtige macOS-Menüleisten-App für Sprachdiktate mit KI-gestützter Textbereinigung.

Diktiere Text per Hotkey, das lokale LLM (MLX) bereinigt Grammatik, Interpunktion und Formatierung — alles lokal auf deinem Mac, ohne Cloud.

## Features

- Globaler Hotkey zum Starten/Stoppen von Diktaten (Standard: `Ctrl+Cmd+E`)
- Lokales LLM (Qwen3-4B via MLX) für Textbereinigung — läuft komplett offline
- Automatische Absätze bei Themenwechsel
- Text-Shortcuts (z.B. "chris email" wird zu deiner E-Mail-Adresse)
- Menüleisten-App mit visuellem Feedback (idle/aufnahme/verarbeitung)
- Diktat-Historie (letzte 20 Einträge)
- Konfigurierbarer Hotkey und Shortcuts über die GUI

## Voraussetzungen

- macOS 13+ (Apple Silicon — M1/M2/M3/M4)

## Schnellinstallation

Ein Befehl installiert alles automatisch (Homebrew, uv, yap, Dependencies, LLM-Modell, App):

```bash
git clone <repo-url>
cd voice_transcript
bash install.sh
```

Das Script:
1. Prüft ob macOS auf Apple Silicon läuft
2. Installiert fehlende Tools (Homebrew, uv, yap) automatisch
3. Lädt Python-Dependencies und das LLM-Modell (~2.5 GB beim ersten Mal)
4. Baut die App, installiert sie in `/Applications` und signiert sie
5. Startet die App

### macOS-Berechtigungen setzen

Nach der Installation (und nach jedem Rebuild):

1. **Systemeinstellungen > Datenschutz & Sicherheit > Eingabeüberwachung**
   - "Voice Transcript" hinzufügen und aktivieren (für den globalen Hotkey)

2. **Systemeinstellungen > Datenschutz & Sicherheit > Bedienungshilfen**
   - "Voice Transcript" hinzufügen und aktivieren (für automatisches Einfügen am Cursor)

### App starten

Über Spotlight, Raycast oder Launchpad nach "Voice Transcript" suchen und starten.

Optional: Unter **Systemeinstellungen > Allgemein > Anmeldeobjekte** hinzufügen für automatischen Start bei Login.

## Manuelle Installation

<details>
<summary>Falls du die Schritte einzeln durchführen möchtest</summary>

### 1. Voraussetzungen installieren

```bash
# uv installieren
curl -LsSf https://astral.sh/uv/install.sh | sh

# yap installieren (braucht Homebrew)
brew install yap
```

### 2. Repository klonen & Dependencies

```bash
git clone <repo-url>
cd voice_transcript
uv sync
```

### 3. LLM-Modell herunterladen

Beim ersten Start wird das Modell automatisch von HuggingFace geladen (~2.5 GB). Alternativ vorab:

```bash
uv run python -c "from mlx_lm import load; load('mlx-community/Qwen3-4B-4bit')"
```

### 4. App bauen und installieren

```bash
bash build.sh
```

Das baut die `.app`, installiert sie in `/Applications` und signiert sie.

</details>

## Benutzung

1. **Hotkey drücken** (Standard: `Ctrl+Cmd+E`) — Aufnahme startet, Menüleisten-Icon wechselt zu rot
2. **Sprechen** — auf Deutsch diktieren
3. **Hotkey erneut drücken** — Aufnahme stoppt, Text wird verarbeitet und am Cursor eingefügt

### Menüleiste

Klick auf das Mikrofon-Icon in der Menüleiste:

- **Diktieren** — Diktat starten/stoppen
- **Historie** — letzte Diktate anzeigen, Klick kopiert in Clipboard
- **Hotkey ändern** — globalen Hotkey anpassen
- **Shortcuts verwalten** — Text-Expansions hinzufügen/bearbeiten
- **Config-Ordner öffnen** — Zugriff auf Konfigurationsdateien

### Text-Shortcuts

Shortcuts sind Wörter/Phrasen die automatisch durch Text ersetzt werden, z.B.:

| Trigger | Ersetzung |
|---------|-----------|
| chris email | chris@example.com |
| chris email privat | chris.private@example.com |
| meine adresse | Musterstraße 1, 12345 Berlin |

Shortcuts können über das Menü "Shortcuts verwalten" bearbeitet werden. Die Datei liegt unter `~/Library/Application Support/VoiceTranscript/shortcuts.json`.

## Konfiguration

Alle Konfigurationsdateien liegen unter `~/Library/Application Support/VoiceTranscript/`:

| Datei | Beschreibung |
|-------|-------------|
| `shortcuts.json` | Text-Shortcuts |
| `settings.json` | Hotkey-Einstellung |
| `history.json` | Diktat-Historie |

## Projektstruktur

```
voice_transcript/
├── dictate.py                    # Raycast Script (optional, als Fallback)
├── install.sh                    # Ein-Klick-Installer (Tools, Modell, App, Autostart)
├── build.sh                      # Build-Script für die .app
├── build_app.spec                # PyInstaller-Konfiguration
├── com.voicetranscript.app.plist # LaunchAgent für den Autostart
├── icon.icns                     # App-Icon
├── pyproject.toml                # Projekt-Definition und Dependencies
├── assets/                       # Menüleisten-Icons (idle/recording/processing)
├── config/
│   └── shortcuts.json            # Beispiel-Shortcuts
└── src/
    └── voice_transcript/
        ├── __init__.py
        ├── __main__.py           # CLI Entry Point
        ├── menubar.py            # Menüleisten-App (rumps)
        ├── main.py               # Diktat-Logik
        ├── llm.py                # LLM-Aufruf (Subprocess)
        ├── llm_server.py         # Persistenter LLM-Server (Unix-Socket)
        ├── llm_worker.py         # MLX LLM Worker
        ├── cleanup.py            # Regex-basierte Textbereinigung
        ├── shortcuts.py          # Text-Expansions
        ├── hotkey.py             # Globaler Hotkey (NSEvent)
        ├── notify.py             # macOS-Notifications
        └── config.py             # Zentrale Konfiguration
```

## Nutzung ohne Menüleisten-App

Das Tool funktioniert auch standalone über die Kommandozeile oder als Raycast-Script:

```bash
# Direkt über CLI
uv run python -m voice_transcript

# Oder als Raycast-Script: dictate.py in Raycast Script-Verzeichnis verlinken
```

## Troubleshooting

### Hotkey funktioniert nicht
- Prüfe ob "Voice Transcript" unter **Eingabeüberwachung** freigegeben ist
- Nach einem Rebuild der App muss die Berechtigung neu gesetzt werden

### "Bitte Bedienungshilfen prüfen"
- "Voice Transcript" unter **Bedienungshilfen** freigeben
- Nach einem Rebuild neu hinzufügen

### Umlaute werden falsch dargestellt
- Stelle sicher, dass dein System-Locale auf UTF-8 steht
- Die App setzt `LANG=de_DE.UTF-8` automatisch

### LLM-Modell lädt nicht
- Prüfe Internetverbindung (nur beim ersten Download nötig)
- Modell-Cache: `~/.cache/huggingface/hub/models--mlx-community--Qwen3-4B-4bit/`

## Technologie

| Komponente | Technologie |
|-----------|-------------|
| Spracherkennung | [yap](https://github.com/timvisee/yap) (Apple Speech Framework) |
| LLM | [MLX](https://github.com/ml-explore/mlx) + Qwen3-4B-4bit |
| Menüleiste | [rumps](https://github.com/jaredks/rumps) |
| Hotkey | PyObjC (NSEvent) |
| Paketmanager | [uv](https://docs.astral.sh/uv/) |
| App-Bundle | [PyInstaller](https://pyinstaller.org/) |
