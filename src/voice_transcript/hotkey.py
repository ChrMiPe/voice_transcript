"""Globaler Hotkey via Carbon RegisterEventHotKey.

Bewusst nicht mehr NSEvent.addGlobalMonitorForEventsMatchingMask_: dieser Weg
braucht Eingabeueberwachung bzw. Bedienungshilfen, liefert bei fehlender
Berechtigung aber trotzdem ein Monitor-Objekt zurueck. Die alte Erfolgspruefung
(`_monitor is not None`) war damit wertlos — der Hotkey schwieg einfach, ohne
dass die App es merken konnte.

RegisterEventHotKey laesst den Hotkey stattdessen beim WindowServer
registrieren. Das braucht *keine* TCC-Berechtigung und meldet Konflikte mit
anderen Apps als Fehlercode zurueck.
"""
import ctypes
import ctypes.util

from voice_transcript.applog import log

_LIB_PATH = (
    ctypes.util.find_library("Carbon")
    or "/System/Library/Frameworks/Carbon.framework/Carbon"
)
try:
    _carbon = ctypes.CDLL(_LIB_PATH)
except OSError:  # pragma: no cover - Carbon fehlt auf keinem macOS
    _carbon = None

try:
    _cf = ctypes.CDLL(
        ctypes.util.find_library("CoreFoundation")
        or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
except OSError:  # pragma: no cover
    _cf = None


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


# OSStatus handler(EventHandlerCallRef, EventRef, void *userData)
_HandlerProc = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)

if _carbon is not None:
    _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    _carbon.InstallEventHandler.argtypes = [
        ctypes.c_void_p,
        _HandlerProc,
        ctypes.c_uint32,
        ctypes.POINTER(_EventTypeSpec),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _carbon.InstallEventHandler.restype = ctypes.c_int32
    _carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        _EventHotKeyID,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _carbon.RegisterEventHotKey.restype = ctypes.c_int32
    _carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    _carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    _carbon.GetEventKind.argtypes = [ctypes.c_void_p]
    _carbon.GetEventKind.restype = ctypes.c_uint32


def _fourcc(text):
    return int.from_bytes(text.encode("ascii"), "big")


_EVENT_CLASS_KEYBOARD = _fourcc("keyb")
_EVENT_HOTKEY_PRESSED = 5
_EVENT_HOTKEY_RELEASED = 6
_HOTKEY_SIGNATURE = _fourcc("vctr")
_ERR_HOTKEY_EXISTS = -9878

# Carbon-Modifier — andere Werte als die NSEvent-Masken.
_CMD, _SHIFT, _OPTION, _CONTROL = 0x0100, 0x0200, 0x0800, 0x1000

MODIFIER_MAP = {
    "cmd": _CMD,
    "command": _CMD,
    "shift": _SHIFT,
    "alt": _OPTION,
    "option": _OPTION,
    "ctrl": _CONTROL,
    "control": _CONTROL,
}

# Tasten ohne druckbares Zeichen — die stehen in keinem Layout und bleiben hier.
NAMED_KEY_CODES = {
    "space": 49, "return": 36, "enter": 36, "escape": 53, "esc": 53, "tab": 48,
    "delete": 51, "backspace": 51, "forwarddelete": 117,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
    "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}

_K_ACTION_DISPLAY = 3
_K_NO_DEAD_KEYS = 1
_layout_cache = None


def _setup_layout_api():
    """Deklariert die TIS/UCKeyTranslate-Aufrufe. False, wenn nicht verfuegbar."""
    try:
        _carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = ctypes.c_void_p
        _carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
        _carbon.LMGetKbdType.restype = ctypes.c_uint8
        _carbon.UCKeyTranslate.argtypes = [
            ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_uint16),
        ]
        _carbon.UCKeyTranslate.restype = ctypes.c_int32
        _cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        _cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        _cf.CFRelease.argtypes = [ctypes.c_void_p]
        return True
    except AttributeError:
        return False


def layout_key_codes(neu_einlesen=False):
    """Zeichen -> Keycode aus dem *aktiven* Tastaturlayout.

    Die frueher hartcodierte Tabelle war ein US-Layout. Auf einem deutschen
    Keyboard sind y und z vertauscht (gemessen: y=6, z=16 statt y=16, z=6) — ein
    Hotkey mit einem der beiden hat also die falsche physische Taste belegt. Und
    Umlaute, Bindestrich oder Punkt kamen ueberhaupt nicht vor.
    """
    global _layout_cache
    if _layout_cache is not None and not neu_einlesen:
        return _layout_cache

    tabelle = {}
    if _carbon is not None and _cf is not None and _setup_layout_api():
        quelle = _carbon.TISCopyCurrentKeyboardLayoutInputSource()
        if quelle:
            try:
                eigenschaft = ctypes.c_void_p.in_dll(
                    _carbon, "kTISPropertyUnicodeKeyLayoutData"
                )
                daten = _carbon.TISGetInputSourceProperty(quelle, eigenschaft)
                if daten:
                    zeiger = _cf.CFDataGetBytePtr(daten)
                    typ = _carbon.LMGetKbdType()
                    for code in range(128):
                        zeichen = _uebersetze(zeiger, typ, code)
                        if zeichen:
                            # setdefault: der erste (niedrigste) Keycode gewinnt,
                            # damit der Ziffernblock die Zahlenreihe nicht verdraengt.
                            tabelle.setdefault(zeichen, code)
            except (AttributeError, OSError) as e:
                log(f"Tastaturlayout nicht lesbar: {type(e).__name__}: {e}")
            finally:
                _cf.CFRelease(quelle)

    _layout_cache = tabelle
    return tabelle


def _uebersetze(zeiger, typ, code):
    """Das Zeichen, das dieser Keycode ohne Modifier erzeugt, oder None."""
    tot = ctypes.c_uint32(0)
    laenge = ctypes.c_ulong(0)
    puffer = (ctypes.c_uint16 * 8)()
    status = _carbon.UCKeyTranslate(
        zeiger, code, _K_ACTION_DISPLAY, 0, typ, _K_NO_DEAD_KEYS,
        ctypes.byref(tot), 8, ctypes.byref(laenge), puffer,
    )
    if status != 0 or laenge.value != 1:
        return None
    zeichen = chr(puffer[0])
    if not zeichen.isprintable() or zeichen.isspace():
        return None
    return zeichen.lower()


def resolve_key(key):
    """Keycode zu einer Tastenangabe, oder None.

    Reihenfolge: benannte Tasten (haben kein Zeichen), dann das aktive Layout,
    zuletzt die US-Tabelle als Rueckfall.
    """
    name = key.lower()
    if name in NAMED_KEY_CODES:
        return NAMED_KEY_CODES[name]
    if len(name) == 1:
        aus_layout = layout_key_codes().get(name)
        if aus_layout is not None:
            return aus_layout
    return KEY_CODE_MAP.get(name)


# Rueckfall, falls sich das Layout nicht auslesen laesst: US-Belegung.
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

# Das CFUNCTYPE-Objekt und die Refs muessen am Leben bleiben, solange Carbon sie
# kennt — sonst raeumt der GC den Trampolin-Code unter dem Handler weg.
_handler_proc = None
_handler_ref = None
_hotkey_ref = None
_callback = None
_release_callback = None


def _dispatch(_call_ref, event, _user_data):
    # Gedrueckt und losgelassen kommen ueber denselben Handler; unterschieden wird
    # an der Event-Art. Das Loslassen braucht Push-to-talk.
    art = _carbon.GetEventKind(event)
    ziel = _callback if art == _EVENT_HOTKEY_PRESSED else _release_callback
    if ziel is not None:
        try:
            ziel()
        except Exception:
            # Eine Exception aus dem Carbon-Dispatcher heraus reisst die
            # Event-Schleife mit — der Hotkey waere danach tot.
            pass
    return 0  # noErr


def _install_handler():
    global _handler_proc, _handler_ref

    if _handler_ref is not None:
        return True

    proc = _HandlerProc(_dispatch)
    specs = (_EventTypeSpec * 2)(
        _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED),
        _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_RELEASED),
    )
    ref = ctypes.c_void_p()
    status = _carbon.InstallEventHandler(
        _carbon.GetApplicationEventTarget(),
        proc,
        2,
        specs,
        None,
        ctypes.byref(ref),
    )
    if status != 0:
        return False

    _handler_proc = proc
    _handler_ref = ref
    return True


def register_hotkey(key, modifiers, callback, on_release=None):
    """Registriert den globalen Hotkey.

    `callback` feuert beim Druecken, `on_release` beim Loslassen — letzteres nur
    fuer Push-to-talk noetig. Beide Ereignisse liefert Carbon ueber denselben
    Handler (Art 5 bzw. 6), geprueft: es kommen beide an.

    Rueckgabe: None bei Erfolg, sonst eine Fehlermeldung fuer den Nutzer.
    """
    global _hotkey_ref, _callback, _release_callback

    if _carbon is None:
        return "Carbon-Framework nicht verfuegbar"

    key_code = resolve_key(key)
    if key_code is None:
        return f"Unbekannte Taste: {key}"

    unknown = [m for m in modifiers if m.lower() not in MODIFIER_MAP]
    if unknown:
        return f"Unbekannter Modifier: {', '.join(unknown)}"

    mask = 0
    for mod in modifiers:
        mask |= MODIFIER_MAP[mod.lower()]
    if not mask:
        return "Mindestens ein Modifier wird benoetigt"

    unregister_hotkey()

    if not _install_handler():
        return "Carbon-Event-Handler konnte nicht installiert werden"

    ref = ctypes.c_void_p()
    status = _carbon.RegisterEventHotKey(
        key_code,
        mask,
        _EventHotKeyID(_HOTKEY_SIGNATURE, 1),
        _carbon.GetApplicationEventTarget(),
        0,
        ctypes.byref(ref),
    )

    if status == _ERR_HOTKEY_EXISTS:
        return f"{format_hotkey(key, modifiers)} ist schon von einer anderen App belegt"
    if status != 0 or not ref.value:
        return f"Hotkey konnte nicht registriert werden (Fehler {status})"

    _hotkey_ref = ref
    _callback = callback
    _release_callback = on_release
    return None


def unregister_hotkey():
    global _hotkey_ref, _callback, _release_callback

    if _hotkey_ref is not None:
        _carbon.UnregisterEventHotKey(_hotkey_ref)
        _hotkey_ref = None
    _callback = None
    _release_callback = None


def format_hotkey(key, modifiers):
    symbols = {
        "cmd": "⌘", "command": "⌘",
        "shift": "⇧",
        "alt": "⌥", "option": "⌥",
        "ctrl": "⌃", "control": "⌃",
    }
    parts = [symbols.get(m.lower(), m) for m in modifiers]
    parts.append(key.upper())
    return "".join(parts)
