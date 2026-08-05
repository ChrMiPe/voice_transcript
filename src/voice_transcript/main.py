import json
import os
import signal
import subprocess
import time
from datetime import datetime

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


def stop_dictation():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGINT)
    except Exception:
        pass
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
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

        stdout, _ = process.communicate()
        raw_text = stdout.strip()

        if on_stop:
            on_stop()

        if not raw_text:
            notify("Yap", "Kein Text erkannt")
            return None

        # Pipeline: Shortcuts -> Regex-Cleanup -> LLM
        text = apply_shortcuts(raw_text)
        text = clean_german_text(text)
        text = llm_polish(text)

        # In Clipboard kopieren
        cb = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        cb.communicate(text.encode("utf-8"))

        time.sleep(0.2)

        # Einfuegen am Cursor (Cmd+V)
        paste_script = 'tell application "System Events" to key code 9 using command down'
        res = subprocess.run(["osascript", "-e", paste_script], capture_output=True, text=True)

        if res.returncode != 0:
            notify("Fehler", "Bitte Bedienungshilfen pruefen")
        else:
            notify("Diktat", "Text eingefuegt!")
            subprocess.Popen(["afplay", "/System/Library/Sounds/Purr.aiff"])

        save_to_history(raw_text, text)

        if on_result:
            on_result(text)

        return text

    except Exception as e:
        notify("Fehler", str(e))
        if on_stop:
            on_stop()
        return None
    finally:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)


def run_dictation():
    """Standalone-Aufruf (Raycast/CLI)."""
    dictate()
