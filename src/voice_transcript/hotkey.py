"""Globale Hotkeys via NSEvent Global Monitor."""
from AppKit import NSEvent, NSKeyDownMask
from Cocoa import (
    NSCommandKeyMask,
    NSShiftKeyMask,
    NSAlternateKeyMask,
    NSControlKeyMask,
)

MODIFIER_MAP = {
    "cmd": NSCommandKeyMask,
    "shift": NSShiftKeyMask,
    "alt": NSAlternateKeyMask,
    "option": NSAlternateKeyMask,
    "ctrl": NSControlKeyMask,
}

KEY_CODE_MAP = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5,
    "h": 4, "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45,
    "o": 31, "p": 35, "q": 12, "r": 15, "s": 1, "t": 17, "u": 32,
    "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
    "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
    "space": 49, "return": 36, "escape": 53, "tab": 48,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
    "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}

_monitor = None


def _build_modifier_mask(modifiers):
    mask = 0
    for mod in modifiers:
        m = MODIFIER_MAP.get(mod.lower())
        if m:
            mask |= m
    return mask


def register_hotkey(key, modifiers, callback):
    global _monitor

    if _monitor is not None:
        unregister_hotkey()

    target_key_code = KEY_CODE_MAP.get(key.lower())
    if target_key_code is None:
        return False

    target_modifiers = _build_modifier_mask(modifiers)

    check_mask = (
        NSCommandKeyMask | NSShiftKeyMask | NSAlternateKeyMask | NSControlKeyMask
    )

    def handler(event):
        if event.keyCode() == target_key_code:
            if (event.modifierFlags() & check_mask) == target_modifiers:
                callback()

    _monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
        NSKeyDownMask, handler
    )
    return _monitor is not None


def unregister_hotkey():
    global _monitor
    if _monitor is not None:
        NSEvent.removeMonitor_(_monitor)
        _monitor = None


def format_hotkey(key, modifiers):
    symbols = {"cmd": "⌘", "shift": "⇧", "alt": "⌥", "option": "⌥", "ctrl": "⌃"}
    parts = [symbols.get(m.lower(), m) for m in modifiers]
    parts.append(key.upper())
    return "".join(parts)
