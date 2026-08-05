"""Log in eine Datei unter Application Support.

Als .app-Bundle laeuft die App ohne Terminal — stdout und stderr landen im
Nirwana. Fehler waren dadurch nur als Benachrichtigung sichtbar, ohne Kontext
und ohne Historie.
"""
import os
from datetime import datetime

from voice_transcript.config import APP_SUPPORT_DIR

LOG_FILE = os.path.join(APP_SUPPORT_DIR, "app.log")
MAX_LOG_BYTES = 1_000_000


def log(message):
    """Schreibt eine Zeile ins Log. Fehler hier duerfen nie durchschlagen."""
    try:
        if os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {message}\n")
    except OSError:
        pass
