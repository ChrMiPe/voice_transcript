"""Text-Shortcuts: laengere Trigger muessen vor kuerzeren gewinnen."""
import json

import pytest

from voice_transcript import shortcuts


@pytest.fixture
def mit_shortcuts(tmp_path, monkeypatch):
    def setzen(daten):
        datei = tmp_path / "shortcuts.json"
        datei.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(shortcuts, "SHORTCUTS_FILE", str(datei))
    return setzen


def test_einfache_ersetzung(mit_shortcuts):
    mit_shortcuts({"chris email": "chris@example.com"})
    assert shortcuts.apply_shortcuts("schick das an chris email bitte") == (
        "schick das an chris@example.com bitte"
    )


def test_gross_klein_egal(mit_shortcuts):
    mit_shortcuts({"chris email": "chris@example.com"})
    assert "chris@example.com" in shortcuts.apply_shortcuts("An CHRIS EMAIL senden")


def test_laengerer_trigger_gewinnt(mit_shortcuts):
    """Sonst frisst „chris email" das Praefix von „chris email privat"."""
    mit_shortcuts({
        "chris email": "chris@example.com",
        "chris email privat": "privat@example.com",
    })
    ergebnis = shortcuts.apply_shortcuts("schick das an chris email privat")
    assert "privat@example.com" in ergebnis
    assert "chris@example.com" not in ergebnis


def test_ohne_datei_unveraendert(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcuts, "SHORTCUTS_FILE", str(tmp_path / "fehlt.json"))
    assert shortcuts.apply_shortcuts("beliebiger text") == "beliebiger text"


def test_kaputte_datei_kostet_kein_diktat(tmp_path, monkeypatch):
    datei = tmp_path / "shortcuts.json"
    datei.write_text("{ kein json")
    monkeypatch.setattr(shortcuts, "SHORTCUTS_FILE", str(datei))
    assert shortcuts.apply_shortcuts("beliebiger text") == "beliebiger text"


@pytest.mark.parametrize("text", ["", None])
def test_leere_eingabe(text, mit_shortcuts):
    mit_shortcuts({"a b": "c"})
    assert shortcuts.apply_shortcuts(text) in ("", None)
