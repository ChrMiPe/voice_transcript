#!/usr/bin/env python3

# @raycast.schemaVersion 1
# @raycast.title Voice Transcribe (German Pro)
# @raycast.mode silent
# @raycast.packageName Dictation
# @raycast.icon 🎙️

import os
import shutil
import subprocess

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MENUBAR_PID_FILE = "/tmp/voice_transcript_menubar.pid"
TRIGGER_FILE = "/tmp/voice_transcript_trigger"


def _find_uv():
    """Raycast startet Skripte in einer Nicht-Login-Shell — $PATH ist minimal,
    deshalb bekannte Installationsorte pruefen und erst danach auf PATH fallen."""
    candidates = (
        "~/.local/bin/uv",
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/uv",
    )
    for candidate in candidates:
        path = os.path.expanduser(candidate)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("uv") or "uv"


UV_PATH = _find_uv()

# Wenn Menubar-App laeuft: Trigger-Datei erstellen
if os.path.exists(MENUBAR_PID_FILE):
    with open(TRIGGER_FILE, "w") as f:
        f.write("1")
else:
    # Fallback: direkt ausfuehren (ohne Menubar-App)
    subprocess.run(
        [UV_PATH, "run", "--project", PROJECT_DIR, "python", "-m", "voice_transcript"],
        cwd=PROJECT_DIR,
    )
