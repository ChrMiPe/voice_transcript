"""Keycode-Auflösung und Hotkey-Formatierung.

Die hartcodierte Tabelle war ein US-Layout. Auf einem deutschen Keyboard sind y
und z vertauscht (gemessen y=6, z=16 statt y=16, z=6) — ein Hotkey mit einem der
beiden hat die falsche physische Taste belegt.
"""
import pytest

from voice_transcript import hotkey
from voice_transcript.hotkey import NAMED_KEY_CODES, format_hotkey, resolve_key


def test_benannte_tasten_kommen_aus_der_tabelle():
    assert resolve_key("space") == NAMED_KEY_CODES["space"]
    assert resolve_key("escape") == NAMED_KEY_CODES["escape"]
    assert resolve_key("f13") == NAMED_KEY_CODES["f13"]


def test_benannte_tasten_sind_gross_klein_unabhaengig():
    assert resolve_key("SPACE") == resolve_key("space")


def test_pfeiltasten_und_f13_plus_sind_bekannt():
    """Die alte Tabelle kannte nur f1..f12 und keine Pfeiltasten."""
    for name in ("left", "right", "up", "down", "f13", "f20", "home", "pagedown"):
        assert resolve_key(name) is not None, name


def test_unbekanntes_ergibt_none():
    assert resolve_key("gibtsnicht") is None


def test_layout_schlaegt_die_us_tabelle(monkeypatch):
    monkeypatch.setattr(hotkey, "_layout_cache", {"y": 6, "z": 16})
    assert resolve_key("y") == 6
    assert resolve_key("z") == 16


def test_rueckfall_auf_die_us_tabelle(monkeypatch):
    """Laesst sich das Layout nicht lesen, muss der Hotkey trotzdem gehen."""
    monkeypatch.setattr(hotkey, "_layout_cache", {})
    assert resolve_key("e") == hotkey.KEY_CODE_MAP["e"]


def test_benannte_taste_gewinnt_vor_dem_layout(monkeypatch):
    """"space" hat im Layout ein Zeichen (Leerzeichen) — die Namenstabelle muss
    zuerst greifen, sonst wird daraus nichts."""
    monkeypatch.setattr(hotkey, "_layout_cache", {"space": 999})
    assert resolve_key("space") == NAMED_KEY_CODES["space"]


# ─── Formatierung ───

@pytest.mark.parametrize("key,mods,erwartet", [
    ("e", ["ctrl", "cmd"], "⌃⌘E"),
    ("e", ["ctrl", "cmd", "shift"], "⌃⌘⇧E"),
    ("space", ["cmd"], "⌘SPACE"),
    ("d", ["alt"], "⌥D"),
    ("d", ["option"], "⌥D"),
    ("d", ["control"], "⌃D"),
])
def test_format_hotkey(key, mods, erwartet):
    assert format_hotkey(key, mods) == erwartet


# ─── Fehlermeldungen von register_hotkey ───

def test_unbekannte_taste_meldet_fehler():
    assert "Unbekannte Taste" in register_ohne_carbon("gibtsnicht", ["cmd"])


def test_modifier_ist_pflicht():
    assert "Modifier" in register_ohne_carbon("e", [])


def test_unbekannter_modifier_meldet_fehler():
    assert "Modifier" in register_ohne_carbon("e", ["meta"])


def register_ohne_carbon(key, mods):
    """register_hotkey bis zur Validierung — ohne echte Registrierung."""
    return hotkey.register_hotkey(key, mods, lambda: None) or ""
