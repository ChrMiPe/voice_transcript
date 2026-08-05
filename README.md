# Voice Transcript

Diktier-App für die macOS-Menüleiste. Hotkey drücken, sprechen, Hotkey drücken — der bereinigte
Text landet am Cursor. Spracherkennung und Textbereinigung laufen **komplett lokal** auf dem Mac,
es verlässt kein Audio und kein Text das Gerät.

```
Hotkey ⌃⌘E → yap (Apple Speech) → Shortcuts → Füllwörter-Filter → LLM (Qwen3-4B) → Clipboard → ⌘V
```

Das lokale LLM korrigiert Grammatik, Interpunktion und Groß-/Kleinschreibung, macht aus
gesprochener Sprache Schriftsprache und setzt bei Themenwechseln Absätze — ohne den Inhalt zu
verändern.

## Features

- **Globaler Hotkey** zum Starten/Stoppen, Standard `⌃⌘E`, in der App änderbar
- **Lokales LLM** (Qwen3-4B via MLX) — offline, keine Cloud, keine API-Keys
- **Text-Shortcuts**: gesprochenes „chris email" wird zur echten Adresse
- **Menüleisten-Feedback**: Icon zeigt Ruhe / Aufnahme / Verarbeitung
- **Historie** der letzten 20 Diktate, per Klick zurück ins Clipboard
- **Graceful Degradation**: fällt das LLM aus, kommt der regex-bereinigte Text statt einer Fehlermeldung

## Voraussetzungen

| | |
|---|---|
| Hardware | Mac mit Apple Silicon (M1 oder neuer) — MLX läuft nicht auf Intel |
| System | macOS 13+ |
| Speicher | ~2,1 GB Modell-Cache + ~360 MB Python-Dependencies |
| Tools | [Homebrew](https://brew.sh), [uv](https://docs.astral.sh/uv/), [yap](https://github.com/finnvoor/yap) — `install.sh` installiert fehlende automatisch |

> **Das Repo bleibt nach der Installation nötig.** Die `.app` enthält MLX absichtlich nicht
> (das Bundle wäre sonst mehrere GB groß), sie startet den LLM-Server per `uv run` aus dem Repo.
> Klone es also an einen dauerhaften Ort und lösche es nicht nach dem Build.

## Installation

### 1. Klonen und installieren

```bash
git clone git@github.com:ChrMiPe/voice_transcript.git ~/projects/voice_transcript
cd ~/projects/voice_transcript
bash install.sh
```

Das Script prüft die Hardware, installiert fehlende Tools (Homebrew, yap, uv), lädt die
Python-Dependencies und das LLM-Modell (~2,1 GB, nur beim ersten Mal), baut die `.app`, installiert
sie nach `/Applications`, signiert sie ad-hoc, richtet den Autostart als LaunchAgent ein und
startet die App.

Der Pfad ist frei wählbar — `install.sh` hinterlegt den Repo-Ort in
`~/Library/Application Support/VoiceTranscript/project_dir`, damit die gebaute App ihn findet.

### 2. Berechtigungen erteilen

macOS lässt Berechtigungen **nicht** per Script setzen — System Integrity Protection schützt die
TCC-Datenbank, jeder direkte `INSERT` scheitert lautlos.

| Bereich | Wofür | Symptom, wenn es fehlt | Wie |
|---------|-------|------------------------|-----|
| **Mikrofon** | Aufnahme durch `yap` | kein Text erkannt | fragt macOS beim ersten Diktat |
| **Spracherkennung** | Apples Speech-Framework | kein Text erkannt | fragt macOS beim ersten Diktat |
| **Bedienungshilfen** | Einfügen am Cursor via `⌘V` | „Text im Clipboard" statt Einfügen | Menü → `⚠ Bedienungshilfen fehlen` |

Der **Hotkey braucht keine Berechtigung**: er wird über Carbon `RegisterEventHotKey` beim
WindowServer registriert, nicht über einen Event-Monitor. Eingabeüberwachung ist damit hinfällig.

Einen **Dialog gibt es für Bedienungshilfen nicht mehr** — aktuelle macOS-Versionen lehnen das ab:

```
tccd: Service kTCCServiceAccessibility does not allow prompting; returning Unknown
tccd: Update Access Record: kTCCServiceAccessibility ... to Denied (System Set)
```

Der Aufruf ist trotzdem nötig, denn erst dieser `Denied`-Eintrag lässt die App in der Liste
erscheinen. Den Rest macht der Menüeintrag `⚠ Bedienungshilfen fehlen`: er öffnet den richtigen
Bereich, dort nur noch den Schalter umlegen.

> **Nach jedem Rebuild neu erteilen.** Die Ad-hoc-Signatur (`codesign --sign -`) hat als Designated
> Requirement einen nackten cdhash:
>
> ```
> $ codesign -d -r- "/Applications/Voice Transcript.app"
> # designated => cdhash H"ff05d124..."
> ```
>
> TCC speichert diese Anforderung mit der Freigabe. Jeder Build erzeugt ein neues Binary und damit
> einen neuen cdhash — die Freigabe passt danach nicht mehr, **obwohl der Schalter weiter aktiviert
> aussieht**. Ein Haken, der nichts tut, ist die zeitraubendste Variante davon; deshalb räumt
> `build.sh` den ungültigen Eintrag per `tccutil reset Accessibility` weg und öffnet den Bereich
> gleich mit.
>
> Dauerhaft lösen ließe sich das nur mit einer stabilen Signatur-Identität (selbst signiertes
> Zertifikat im Schlüsselbund statt `-`). `build.sh` nimmt sie über
> `VOICE_TRANSCRIPT_SIGN_IDENTITY` entgegen und überspringt dann das Zurücksetzen. Ohne die
> Variable bleibt alles ad-hoc — kein Eingriff in den Schlüsselbund.

### 3. Shortcuts übernehmen (optional)

Die App startet ohne Shortcuts. Die Beispieldatei aus dem Repo aktivieren:

```bash
cp config/shortcuts.json ~/Library/Application\ Support/VoiceTranscript/
```

Danach die App neu starten. Alternativ Shortcuts einzeln über das Menü anlegen.

### Manuelle Installation

<details>
<summary>Einzelschritte, falls du <code>install.sh</code> nicht ausführen willst</summary>

```bash
# 1. Tools
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install yap

# 2. Dependencies
uv sync

# 3. LLM-Modell vorab laden (sonst passiert es beim ersten Start)
uv run python -c "from mlx_lm import load; load('mlx-community/Qwen3-4B-4bit')"

# 4. App bauen, installieren, signieren, Repo-Pfad hinterlegen
bash build.sh
```

`install.sh` macht darüber hinaus nur noch den Autostart:

```bash
cp com.voicetranscript.app.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.voicetranscript.app.plist
```

</details>

## Benutzung

1. **`⌃⌘E` drücken** — Ton („Tink"), Icon wird zum Aufnahme-Symbol
2. **Auf Deutsch sprechen**
3. **`⌃⌘E` erneut drücken** — Icon zeigt Verarbeitung, dann wird der Text am Cursor eingefügt
   (Ton „Purr")

Der Text liegt zusätzlich immer im Clipboard — wenn das Einfügen scheitert, reicht `⌘V`.

### Menü

Klick auf das Mikrofon-Icon:

| Eintrag | Funktion |
|---------|----------|
| **Diktieren (⌃⌘E)** | Titel folgt dem Zustand: „Aufnahme stoppen" während der Aufnahme, „Wird verarbeitet…" während der Bereinigung |
| **Letzte Diktate** | Untermenü, letzte 10 Diktate mit Uhrzeit; Klick kopiert ins Clipboard |
| **Einstellungen** | Untermenü: `Hotkey ändern…`, `Shortcuts verwalten…`, `Config-Ordner öffnen`, `Historie löschen…` |
| **LLM-Status** | ausgegraut, alle 5 s aktualisiert — siehe Tabelle unten |
| **⚠ Bedienungshilfen fehlen** | erscheint **nur**, solange die Berechtigung fehlt; Klick öffnet den Bereich in den Systemeinstellungen |
| **Beenden (⌘Q)** | App und LLM-Server beenden |

Der Hotkey steht als Text im Titel, weil ein echtes Key-Equivalent am `NSMenuItem` bei offenem
Menü zusätzlich zum globalen Hotkey feuern und das Diktat doppelt starten würde. `⌘Q` bei
„Beenden" ist dagegen ein echtes Kürzel und wird von macOS rechtsbündig gesetzt.

Die Statuszeile zeigt, ob die LLM-Bereinigung tatsächlich greift — ohne sie merkt man einen
ausgefallenen Server nur daran, dass der Text schlechter aussieht. Ein Symbol steht nur dort, wo
etwas zu tun ist:

| Anzeige | Bedeutung |
|---------|-----------|
| `LLM bereit` | Server läuft, Modell geladen, Socket erreichbar |
| `LLM lädt Modell…` | Server gestartet, Modell noch nicht im RAM (dauert nach dem App-Start etwas) |
| `⚠ LLM nicht erreichbar` | Server läuft nicht — es kommt nur der Füllwörter-Filter (siehe Troubleshooting) |
| `LLM-Bereinigung aus` | `LLM_ENABLED = False` in `config.py` |

### Das Menüleisten-Icon

Die Zustände nutzen SF Symbols, keine mitgelieferten Bilder — Vektoren bleiben in jeder Größe
scharf und macOS passt sie an Hell/Dunkel an:

| Zustand | Symbol | Darstellung |
|---------|--------|-------------|
| Ruhe | `mic` | einfarbig, folgt der Menüleiste |
| Aufnahme | `mic.fill` | gefüllt und in Systemrot |
| Verarbeitung | `waveform` | einfarbig, folgt der Menüleiste |

Rot funktioniert nur, weil ein eingefärbtes SF Symbol als `template=False` zurückkommt. Die
früheren PNGs in `assets/` enthielten zwar einen roten Punkt, doch bei `template=True` verwirft
macOS jede Farbe und zeichnet nur die Alpha-Maske — das Rot war nie zu sehen. Die PNGs bleiben als
Fallback liegen, falls das System ein Symbol nicht kennt.

### Text-Shortcuts

Wörter, die nach der Spracherkennung ersetzt werden — praktisch für alles, was man nicht diktieren
mag. Vergleich ist case-insensitiv, längere Trigger gewinnen (`chris email privat` vor `chris email`).

| Trigger | Ersetzung |
|---------|-----------|
| `chris email` | `chris@example.com` |
| `meine adresse` | `Musterstraße 1, 12345 Berlin` |

## Konfiguration

Alles unter `~/Library/Application Support/VoiceTranscript/` (Menü → „Config-Ordner öffnen"):

| Datei | Inhalt |
|-------|--------|
| `shortcuts.json` | Text-Shortcuts (`{"trigger": "ersetzung"}`) |
| `settings.json` | Hotkey |
| `history.json` | letzte 20 Diktate (Rohtext + Ergebnis + Zeitstempel) |
| `project_dir` | Repo-Pfad, von `build.sh` geschrieben |
| `app.log` | Hotkey-Registrierung, `yap`-Fehler, abgelehntes Einfügen — die App hat als Bundle kein Terminal, hier landen die Details |

Modell, Temperatur und der System-Prompt für die Bereinigung stehen in
`src/voice_transcript/config.py`. `LLM_ENABLED = False` schaltet das LLM ab, dann bleibt nur der
Füllwörter-Filter. Nach Änderungen: `bash build.sh`.

Findet die App `uv` oder `yap` nicht, lassen sich die Pfade überschreiben:

| Variable | Zweck |
|----------|-------|
| `VOICE_TRANSCRIPT_UV` | Pfad zur `uv`-Binary |
| `VOICE_TRANSCRIPT_YAP` | Pfad zur `yap`-Binary |
| `VOICE_TRANSCRIPT_PROJECT_DIR` | Repo-Pfad (überschreibt `project_dir`) |

## Wie es funktioniert

Ein Diktat durchläuft vier Stufen (`main.py: dictate()`):

1. **Aufnahme** — `yap dictate` als Subprozess, Transkript kommt über stdout. Ein zweiter
   Hotkey-Druck schickt `SIGINT` an die in `/tmp/yap_dictation.pid` notierte PID. Ob der Prozess
   dort noch lebt, wird geprüft — eine verwaiste PID-Datei aus einem Absturz hätte sonst den
   nächsten Hotkey-Druck verschluckt.
2. **Shortcuts** — `shortcuts.py` ersetzt Trigger-Phrasen.
3. **Füllwörter** — `cleanup.py` entfernt per Regex „ähm", „also", „quasi" und Ähnliches.
4. **LLM** — `llm.py` schickt den Text an den lokalen Server; das Ergebnis geht ins Clipboard und
   wird per AppleScript (`System Events`, `⌘V`) eingefügt.

**Der LLM-Server** (`llm_server.py`) hält das Modell dauerhaft im RAM und lauscht auf dem
Unix-Socket `/tmp/voice_transcript_llm.sock` — ohne ihn würde jedes Diktat mehrere Sekunden aufs
Modell-Laden warten. Die Menüleisten-App startet ihn beim Launch und beendet ihn beim Quit.

Der Textbereinigung ist bewusst misstraut: Antworten des Modells werden von `<think>`-Blöcken
befreit, und ein Ergebnis, das kürzer als 30 % der Eingabe ist, gilt als Halluzination — dann wird
der ursprüngliche Text behalten. Fällt der Server ganz aus, greift ein Subprozess-Fallback
(`llm_worker.py`); scheitert auch der, bekommst du den regex-bereinigten Text.

## Entwicklung

```bash
uv sync                                    # Dependencies
uv run voice-transcript                    # Menüleisten-App aus dem Quellbaum
uv run python -m voice_transcript          # ein einzelnes Diktat, ohne Menüleiste
uv run python -m voice_transcript.llm_server   # LLM-Server im Vordergrund (zeigt Fehler)
bash build.sh                              # neu bauen und nach /Applications installieren
```

Aus dem Quellbaum gestartet braucht die App keine Berechtigungen für „Voice Transcript", sondern für
das Terminal, aus dem sie läuft.

### Projektstruktur

```
voice_transcript/
├── install.sh                    # Ein-Klick-Installer (Tools, Modell, Build, Autostart)
├── build.sh                      # Rebuild + Installation nach /Applications
├── build_app.spec                # PyInstaller-Konfiguration (MLX bewusst exkludiert)
├── com.voicetranscript.app.plist # LaunchAgent für den Autostart
├── dictate.py                    # Raycast-Script (Fallback ohne Menüleisten-App)
├── icon.icns                     # App-Icon
├── assets/                       # PNG-Fallback-Icons, falls SF Symbols fehlen
├── config/shortcuts.json         # Beispiel-Shortcuts (manuell kopieren)
└── src/voice_transcript/
    ├── menubar.py                # Menüleisten-App (rumps), Einstiegspunkt
    ├── main.py                   # Diktat-Ablauf und Historie
    ├── hotkey.py                 # globaler Hotkey (Carbon), Keycode-Tabelle
    ├── permissions.py            # Bedienungshilfen-Status abfragen und anfordern
    ├── applog.py                 # Log nach Application Support
    ├── llm.py                    # Client: Socket + Subprozess-Fallback
    ├── llm_server.py             # persistenter MLX-Server (Unix-Socket)
    ├── llm_worker.py             # Einmal-Aufruf des Modells (Fallback)
    ├── cleanup.py                # Füllwörter-Filter
    ├── shortcuts.py              # Text-Expansion
    ├── notify.py                 # macOS-Benachrichtigungen
    └── config.py                 # Pfade, Modell-Parameter, System-Prompt
```

## Troubleshooting

**Hotkey tut nichts, Menü funktioniert**
Der Hotkey braucht keine Berechtigung — eine andere App belegt die Kombination. Die App meldet das
beim Start als Benachrichtigung und notiert es im Log
(`~/Library/Application Support/VoiceTranscript/app.log`). Über `Hotkey ändern…` eine andere wählen.
Der Menütitel zeigt immer die aktuell registrierte Kombination.

**„Text im Clipboard — bitte ⌘V drücken"**
Bedienungshilfen fehlen. Im Menü `⚠ Bedienungshilfen fehlen` anklicken, in der geöffneten Liste
„Voice Transcript" aktivieren. Nach einem Rebuild ist der Eintrag ungültig, obwohl er aktiviert
aussieht — dann aus- und wieder einschalten (siehe „Berechtigungen erteilen"). Der Text liegt im
Clipboard, `⌘V` funktioniert also weiterhin.

Das Einfügen prüft die Berechtigung **nicht** vorab, sondern versucht es und wertet erst den
Fehlschlag aus: `AXIsProcessTrusted` liefert im laufenden Prozess einen veralteten Wert, ein
Vorab-Check hätte das Einfügen also auch nach dem Erteilen weiter blockiert. Der genaue Grund steht
in `app.log`.

**„Kein Text erkannt"**
`yap` liefert nichts. Direkt testen:

```bash
yap dictate            # sprechen, dann Ctrl+C
```

Kommt hier nichts, fehlen Mikrofon- oder Spracherkennungs-Rechte, oder `yap` ist nicht installiert
(`brew install yap`).

**Text ist da, aber nur grob bereinigt**
Der LLM-Server läuft nicht — Interpunktion und Absätze fehlen dann. Prüfen:

```bash
ls -l /tmp/voice_transcript_llm.sock          # Socket vorhanden?
cat /tmp/voice_transcript_llm.pid             # Server-PID
uv run python -m voice_transcript.llm_server  # im Vordergrund starten, Fehler sichtbar
```

Häufigste Ursache: das Repo wurde verschoben oder gelöscht, `project_dir` zeigt ins Leere. Dann
`bash build.sh` erneut ausführen.

**LLM-Modell lädt nicht**
Beim ersten Start braucht es Internet für ~2,1 GB. Cache liegt unter
`~/.cache/huggingface/hub/models--mlx-community--Qwen3-4B-4bit/`.

**App startet nicht / kein Icon**
Vermutlich läuft schon eine Instanz — die App verhindert Doppelstarts über
`/tmp/voice_transcript_menubar.pid`. Aufräumen:

```bash
pkill -f "Voice Transcript"; rm -f /tmp/voice_transcript_menubar.pid
open "/Applications/Voice Transcript.app"
```

**Umlaute falsch**
Die App setzt `LANG=de_DE.UTF-8` selbst. Falls doch: System-Locale auf UTF-8 prüfen.

## Grenzen

- **Deutsch.** System-Prompt und Füllwörter-Filter sind auf Deutsch ausgelegt.
- **Apple Silicon.** MLX gibt es nicht für Intel-Macs.
- **Das Repo muss liegen bleiben** — siehe oben, die `.app` allein genügt nicht.
- **`dictate.py` ist nur ein Fallback.** Läuft die Menüleisten-App, schreibt das Script lediglich
  `/tmp/voice_transcript_trigger` — diese Datei liest derzeit niemand, das Diktat startet also
  nicht. Ohne laufende App diktiert das Script korrekt. Für Raycast ist der globale Hotkey der
  verlässliche Weg.

## Technologie

| Komponente | Technologie |
|-----------|-------------|
| Spracherkennung | [yap](https://github.com/finnvoor/yap) (Apple Speech Framework, on-device) |
| LLM | [MLX](https://github.com/ml-explore/mlx) + [Qwen3-4B-4bit](https://huggingface.co/mlx-community/Qwen3-4B-4bit) |
| Menüleiste | [rumps](https://github.com/jaredks/rumps) |
| Hotkey | Carbon `RegisterEventHotKey` (via `ctypes`, ohne Berechtigung) |
| Berechtigungs-Status | `AXIsProcessTrusted` (via `ctypes`, kein zusätzliches pyobjc-Paket) |
| Paketmanager | [uv](https://docs.astral.sh/uv/) |
| App-Bundle | [PyInstaller](https://pyinstaller.org/) |
