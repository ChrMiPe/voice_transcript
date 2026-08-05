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

        # In Clipboard kopieren
        cb = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        cb.communicate(text.encode("utf-8"))

        save_to_history(raw_text, text)
        _paste_at_cursor()

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


def _paste_at_cursor():
    """Fuegt den Clipboard-Inhalt am Cursor ein (⌘V).

    Ohne Bedienungshilfen-Recht wird der AppleEvent an System Events abgelehnt.
    Vorher pruefen statt hinterher raten: der Nutzer bekommt dann die richtige
    Meldung, und der Text liegt ohnehin schon im Clipboard.
    """
    if not permissions.is_trusted():
        log("Einfuegen uebersprungen — keine Bedienungshilfen-Rechte")
        notify("Text im Clipboard", "Bedienungshilfen fehlen — bitte ⌘V druecken")
        return False

    time.sleep(0.2)

    paste_script = 'tell application "System Events" to key code 9 using command down'
    res = subprocess.run(["osascript", "-e", paste_script], capture_output=True, text=True)

    if res.returncode != 0:
        detail = (res.stderr or "").strip()
        log(f"Einfuegen fehlgeschlagen (exit {res.returncode}): {detail or '—'}")
        notify("Text im Clipboard", "Einfuegen abgelehnt — bitte ⌘V druecken")
        return False

    notify("Diktat", "Text eingefuegt!")
    subprocess.Popen(["afplay", "/System/Library/Sounds/Purr.aiff"])
    return True


def run_dictation():
    """Standalone-Aufruf (Raycast/CLI)."""
    dictate()
