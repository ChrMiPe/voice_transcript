#!/usr/bin/env python3

# @raycast.schemaVersion 1
# @raycast.title Voice Transcribe (German Pro)
# @raycast.mode silent
# @raycast.packageName Dictation
# @raycast.icon 🎙️

import os
import subprocess

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
UV_PATH = "/Library/Frameworks/Python.framework/Versions/3.13/bin/uv"
MENUBAR_PID_FILE = "/tmp/voice_transcript_menubar.pid"
TRIGGER_FILE = "/tmp/voice_transcript_trigger"

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
