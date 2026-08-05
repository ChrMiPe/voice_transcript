import json
import os
import re

from voice_transcript.config import SHORTCUTS_FILE


def load_shortcuts():
    path = SHORTCUTS_FILE
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
