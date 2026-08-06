"""Der Fuellwort-Filter darf keine Bedeutung mehr zerstoeren.

Frueher entfernte cleanup.py auch „also", „halt", „ja", „eigentlich", „eben",
„quasi" und „nicht wahr" — kontextblind, und *vor* dem LLM, das nichts davon retten
konnte. Diese Tests halten fest, dass nur echte Verzoegerungslaute weichen.
"""
import pytest

from voice_transcript.cleanup import clean_german_text


@pytest.mark.parametrize("gesprochen", [
    # Genau die Faelle, die der alte Filter kaputt gemacht hat
    "das ist nicht wahr",
    "ich halt das für richtig",
    "ja das machen wir so",
    "eigentlich hatte ich etwas anderes vor",
    "eben deshalb rufe ich an",
    "also gut dann fangen wir an",
    "das ist quasi fertig",
    "das war sozusagen der plan",
    "irgendwie passt das schon",
])
def test_bedeutungstragende_woerter_bleiben(gesprochen):
    assert clean_german_text(gesprochen) == gesprochen


@pytest.mark.parametrize("gesprochen,erwartet", [
    ("ähm das ist ein test", "das ist ein test"),
    ("äh das ist ein test", "das ist ein test"),
    ("öhm das ist ein test", "das ist ein test"),
    ("hm das ist ein test", "das ist ein test"),
    ("hmm das ist ein test", "das ist ein test"),
    ("ÄHM das ist ein test", "das ist ein test"),
    ("das ist ähm ein test", "das ist ein test"),
])
def test_verzoegerungslaute_weichen(gesprochen, erwartet):
    assert clean_german_text(gesprochen) == erwartet


def test_mhm_bleibt_denn_es_heisst_ja():
    assert "mhm" in clean_german_text("mhm das passt")


@pytest.mark.parametrize("gesprochen,erwartet", [
    # Nach dem Entfernen darf keine Luecke vor dem Satzzeichen stehen
    ("das ist ähm, gut", "das ist, gut"),
    ("das ist ähm. gut", "das ist. gut"),
    ("doppelte  leerzeichen", "doppelte leerzeichen"),
])
def test_luecken_werden_aufgeraeumt(gesprochen, erwartet):
    assert clean_german_text(gesprochen) == erwartet


@pytest.mark.parametrize("eingabe", ["", None])
def test_leere_eingabe(eingabe):
    assert clean_german_text(eingabe) == ""
