import json
import os
import re

from voice_transcript.applog import log
from voice_transcript.config import SHORTCUTS_FILE


def load_shortcuts():
    """Shortcuts aus der Konfiguration. Fehler duerfen kein Diktat kosten.

    Ein Tippfehler in der von Hand gepflegten shortcuts.json hat vorher die ganze
    Bereinigung mitgerissen — der Nutzer sah nur "Fehler" und keinen Text.
    """
    path = SHORTCUTS_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            daten = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Shortcuts nicht lesbar: {type(e).__name__}: {e}")
        return {}
    if not isinstance(daten, dict):
        log("Shortcuts haben unerwartetes Format — erwartet wird ein Objekt")
        return {}
    return daten


def apply_shortcuts(text):
    if not text:
        return text

    shortcuts = load_shortcuts()
    if not shortcuts:
        return text

    # Laengere Patterns zuerst, damit "chris email privat" vor "chris email" matched
    sorted_keys = sorted(shortcuts.keys(), key=len, reverse=True)

    for trigger in sorted_keys:
        replacement = shortcuts[trigger]
        pattern = re.compile(re.escape(trigger), re.IGNORECASE)
        text = pattern.sub(replacement, text)

    return text
