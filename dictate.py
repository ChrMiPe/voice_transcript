#!/usr/bin/env python3

# @raycast.schemaVersion 1
# @raycast.title Voice Transcribe (German Pro)
# @raycast.mode silent
# @raycast.packageName Dictation
# @raycast.icon 🎙️

"""Raycast-Script fuer ein einzelnes Diktat.

Frueher schrieb dieses Script bei laufender Menueleisten-App eine Trigger-Datei
nach /tmp/voice_transcript_trigger — die hat nie jemand gelesen, das Diktat startete
also nicht. Der Pfad ist entfernt: das Script diktiert jetzt immer selbst.

Fuer den Alltag ist der globale Hotkey der Weg; er braucht seit dem Umstieg auf
Carbon keine Berechtigung mehr (siehe src/voice_transcript/hotkey.py).
"""

import os
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


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


result = subprocess.run(
    [_find_uv(), "run", "--project", PROJECT_DIR, "python", "-m", "voice_transcript"],
    cwd=PROJECT_DIR,
)
sys.exit(result.returncode)
