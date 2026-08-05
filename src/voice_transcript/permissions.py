"""Bedienungshilfen-Status (Accessibility) abfragen.

Gebraucht wird das nur fuer das Einfuegen am Cursor (⌘V). Der Hotkey selbst
kommt seit dem Umstieg auf Carbon ohne jede Berechtigung aus — siehe hotkey.py.

Zugriff per ctypes statt pyobjc-framework-Quartz: fuer zwei Funktionsaufrufe
waere das eine zusaetzliche Dependency und mehr Gewicht im App-Bundle.
"""
import ctypes
import ctypes.util
import subprocess

import objc
from Foundation import NSDictionary

_LIB_PATH = ctypes.util.find_library("ApplicationServices")
try:
    _ax = ctypes.CDLL(_LIB_PATH) if _LIB_PATH else None
except OSError:  # pragma: no cover
    _ax = None

if _ax is not None:
    _ax.AXIsProcessTrusted.restype = ctypes.c_bool
    _ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    _ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool

ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)


def is_trusted():
    """True, wenn die App am Cursor tippen darf (Bedienungshilfen erteilt)."""
    if _ax is None:
        return False
    return bool(_ax.AXIsProcessTrusted())


def ensure_listed():
    """Sorgt dafuer, dass die App in der Bedienungshilfen-Liste auftaucht.

    Der Name sagt bewusst nicht "request": aktuelle macOS-Versionen zeigen fuer
    Bedienungshilfen *keinen* Dialog mehr. tccd protokolliert das unmissverstaendlich:

        Service kTCCServiceAccessibility does not allow prompting; returning Unknown
        Update Access Record: kTCCServiceAccessibility ... to Denied (System Set)

    Genau dieser Eintrag ist der Zweck des Aufrufs — mit ihm erscheint die App in
    den Systemeinstellungen und der Nutzer muss nur den Schalter umlegen. Ohne ihn
    fehlt sie in der Liste und muesste per "+" aus /Applications gesucht werden.
    """
    if _ax is None:
        return False
    options = NSDictionary.dictionaryWithObject_forKey_(
        True, "AXTrustedCheckOptionPrompt"
    )
    return bool(_ax.AXIsProcessTrustedWithOptions(objc.pyobjc_id(options)))


def open_settings():
    subprocess.run(["open", ACCESSIBILITY_PANE])
