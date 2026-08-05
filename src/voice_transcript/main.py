import json
import os
import signal
import subprocess
import time
from datetime import datetime

from voice_transcript import permissions
from voice_transcript.applog import log
from voice_transcript.cleanup import clean_german_text
from voice_transcript.config import HISTORY_FILE, MAX_HISTORY, PID_FILE, YAP_PATH
from voice_transcript.llm import llm_polish
from voice_transcript.notify import notify
from voice_transcript.shortcuts import apply_shortcuts

# UTF-8 sicherstellen (PyInstaller-Apps haben oft kein LANG gesetzt)
os.environ.setdefault("LANG", "de_DE.UTF-8")
os.environ.setdefault("LC_ALL", "de_DE.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Jeder Hilfsprozess im Diktat-Pfad braucht eine Obergrenze. Blieb einer haengen
# — System Events klemmt nach Schlaf oder Abmeldung gern —, blieb der Zustand auf
# "processing" stehen und die recording-Property sperrte jedes weitere Diktat aus.
# Die einzige Ausnahme ist `yap dictate` selbst: dessen Laufzeit bestimmt der
# Nutzer, indem er den Hotkey erneut drueckt.
OSASCRIPT_TIMEOUT = 10
CLIPBOARD_TIMEOUT = 5


def save_to_history(raw, result):
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

    history.insert(0, {
        "raw": raw,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    })
    history = history[:MAX_HISTORY]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _clear_pid():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def _running_pid():
    """PID der laufenden yap-Aufnahme, oder None.

    Prueft, ob der Prozess ueberhaupt noch lebt: nach einem Absturz bleibt die
    PID-Datei liegen, und die alte Fassung meldete jede vorhandene Datei als
    "laeuft". Der naechste Hotkey-Druck hat dann nur diese Leiche gestoppt statt
    aufzunehmen — erst der zweite Druck startete ein Diktat.
    """
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        _clear_pid()
        return None

    try:
        os.kill(pid, 0)
    except OSError:
        log(f"Verwaiste PID-Datei aufgeraeumt (PID {pid})")
        _clear_pid()
        return None

    return pid


def stop_dictation():
    pid = _running_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGINT)
    except OSError as e:
        log(f"SIGINT an yap (PID {pid}) fehlgeschlagen: {e}")
    finally:
        _clear_pid()
    return True


def dictate(on_start=None, on_stop=None, on_result=None):
    """Fuehrt ein Diktat durch. Callbacks fuer UI-Updates."""
    if stop_dictation():
        if on_stop:
            on_stop()
        return None

    if on_start:
        on_start()
    notify("Yap Dictation", "Starte Diktat...")
    subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])

    try:
        process = subprocess.Popen(
            [YAP_PATH, "dictate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

        with open(PID_FILE, "w") as f:
            f.write(str(process.pid))

        stdout, stderr = process.communicate()
        raw_text = stdout.strip()

        if on_stop:
            on_stop()

        if not raw_text:
            # yap meldet fehlenden Mikrofon- oder Spracherkennungs-Zugriff auf
            # stderr — ohne Log war das von "nichts gesagt" nicht zu trennen.
            detail = (stderr or "").strip()
            log(f"yap ohne Ergebnis (exit {process.returncode}): {detail or '—'}")
            notify("Yap", detail.splitlines()[0][:120] if detail else "Kein Text erkannt")
            return None

        # Pipeline: Shortcuts -> Regex-Cleanup -> LLM
        text = apply_shortcuts(raw_text)
        text = clean_german_text(text)
        text = llm_polish(text)

        # Vor dem Ueberschreiben sichern, damit ein Diktat nicht die Zwischenablage
        # des Nutzers verbraucht.
        previous_clipboard = _read_clipboard()

        write_clipboard(text.encode("utf-8"))
        save_to_history(raw_text, text)

        if _paste_at_cursor():
            _restore_clipboard(previous_clipboard)
        # Bei Fehlschlag bleibt der diktierte Text liegen — dann ist ⌘V die
        # Rettung und darf nicht weggeraeumt werden.

        if on_result:
            on_result(text)

        return text

    except Exception as e:
        log(f"Diktat fehlgeschlagen: {type(e).__name__}: {e}")
        notify("Fehler", str(e))
        if on_stop:
            on_stop()
        return None
    finally:
        _clear_pid()


PASTE_SCRIPT = 'tell application "System Events" to key code 9 using command down'

# Zeit, die die Ziel-App braucht, um das ⌘V zu verarbeiten. osascript kehrt
# zurueck, sobald System Events das Tastenereignis abgeschickt hat — die Ziel-App
# liest die Zwischenablage erst danach. Wird hier zu kurz gewartet, stellen wir
# den alten Inhalt wieder her, bevor er ausgelesen wurde.
PASTE_SETTLE_SECONDS = 0.5


def write_clipboard(data):
    """Schreibt Bytes in die Zwischenablage. True bei Erfolg."""
    try:
        cb = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        cb.communicate(data, timeout=CLIPBOARD_TIMEOUT)
        return cb.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log(f"pbcopy fehlgeschlagen: {type(e).__name__}: {e}")
        return False


def _read_clipboard():
    """Aktuellen Zwischenablage-Inhalt als Bytes, oder None.

    pbpaste liefert nur Klartext. Bild- oder Rich-Content kommt als leerer bzw.
    unbrauchbarer Wert zurueck — den wuerden wir beim Wiederherstellen durch
    Nichts ersetzen und damit mehr kaputt machen als reparieren. Deshalb gilt
    nur nicht-leerer Text als sicherungswuerdig.
    """
    try:
        res = subprocess.run(["pbpaste"], capture_output=True, timeout=CLIPBOARD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"pbpaste fehlgeschlagen: {type(e).__name__}: {e}")
        return None

    if res.returncode != 0 or not res.stdout:
        return None
    return res.stdout


def _restore_clipboard(data):
    """Stellt den gesicherten Inhalt wieder her.

    Der diktierte Text ist ueber "Letzte Diktate" weiter erreichbar, es geht also
    nichts verloren.
    """
    if not data:
        return
    time.sleep(PASTE_SETTLE_SECONDS)
    write_clipboard(data)


def _paste_at_cursor():
    """Fuegt den Clipboard-Inhalt am Cursor ein (⌘V).

    Erst einfuegen, dann bei Misserfolg nach dem Grund fragen — nicht umgekehrt.
    AXIsProcessTrusted liefert innerhalb eines laufenden Prozesses einen
    gecachten Wert: erteilt man die Bedienungshilfen bei laufender App, bleibt
    die Antwort "nein", bis die App neu startet. Ein Vorab-Check haette das
    Einfuegen also auch dann verhindert, wenn es funktioniert haette.
    """
    time.sleep(0.2)

    try:
        res = subprocess.run(
            ["osascript", "-e", PASTE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=OSASCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Ohne Timeout blieb der Diktat-Thread hier haengen, wenn System Events
        # klemmt — und mit ihm der Zustand "processing", der jedes weitere
        # Diktat aussperrt.
        log(f"Einfuegen abgebrochen — osascript nach {OSASCRIPT_TIMEOUT}s ohne Antwort")
        notify("Text im Clipboard", "Einfügen hängt — bitte ⌘V drücken")
        return False
    except OSError as e:
        log(f"Einfuegen fehlgeschlagen: {type(e).__name__}: {e}")
        notify("Text im Clipboard", "Einfügen nicht möglich — bitte ⌘V drücken")
        return False

    if res.returncode == 0:
        notify("Diktat", "Text eingefuegt!")
        subprocess.Popen(["afplay", "/System/Library/Sounds/Purr.aiff"])
        return True

    detail = (res.stderr or "").strip().replace("\n", " ")
    trusted = permissions.is_trusted()
    log(f"Einfuegen fehlgeschlagen (exit {res.returncode}, trusted={trusted}): {detail or '—'}")

    if not trusted:
        notify("Text im Clipboard", "Bedienungshilfen fehlen — bitte ⌘V drücken")
    else:
        notify("Text im Clipboard", "Einfügen abgelehnt — bitte ⌘V drücken")
    return False


def run_dictation():
    """Standalone-Aufruf (Raycast/CLI)."""
    dictate()
