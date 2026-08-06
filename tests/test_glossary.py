"""Koelner Phonetik und der Glossar-Abgleich.

Die Referenzwerte fuer koelner_phonetik() sind die kanonischen Beispiele der
Methode (Postel 1969) — sie halten die Implementierung fest, ohne dass man ihr
glauben muss.
"""
import json

import pytest

from voice_transcript import glossary
from voice_transcript.glossary import correct, koelner_phonetik, whisper_prompt

FACHBEGRIFFE = [
    "Kubernetes", "Idempotenz", "idempotent", "Active Directory",
    "Continuous Integration", "Bilanzkreis", "Netzentgelt", "Lastgang",
    "Observability", "Authentifizierung",
]


# ─── Koelner Phonetik ───

@pytest.mark.parametrize("wort,code", [
    ("Müller", "657"),
    ("Wikipedia", "3412"),
    ("Breschnew", "17863"),
    ("Meier", "67"),
    ("Mayer", "67"),
    ("Maier", "67"),
    ("Mayr", "67"),
])
def test_kanonische_codes(wort, code):
    assert koelner_phonetik(wort) == code


def test_aehnlich_klingendes_faellt_zusammen():
    assert koelner_phonetik("Kubernetes") == koelner_phonetik("Kuberneetes")
    assert koelner_phonetik("Idempotenz") == koelner_phonetik("Iddempotenz")


@pytest.mark.parametrize("wort", ["", "123", "!!!", "h"])
def test_nicht_codierbares_ergibt_leeren_string(wort):
    assert koelner_phonetik(wort) == ""


def test_umlaute_werden_normalisiert():
    assert koelner_phonetik("Müller") == koelner_phonetik("Mueller")


# ─── Glossar-Abgleich ───

@pytest.mark.parametrize("gesprochen,erwartet", [
    ("wir müssen das auf kuberneetes deployen", "Kubernetes"),
    ("die iddempotenz fehlt", "Idempotenz"),
    ("wir brauchen mehr obserwability", "Observability"),
    ("die continious integration ist rot", "Continuous Integration"),
])
def test_verhoerungen_werden_korrigiert(gesprochen, erwartet):
    assert erwartet in correct(gesprochen, FACHBEGRIFFE)


@pytest.mark.parametrize("gesprochen,erwartet", [
    # Die Erkennung trennt zusammengesetzte Begriffe gern
    ("der bilanz kreis stimmt nicht", "Bilanzkreis"),
    ("das netz entgelt ist gestiegen", "Netzentgelt"),
    ("der last gang sieht komisch aus", "Lastgang"),
])
def test_getrennte_komposita_werden_zusammengefuehrt(gesprochen, erwartet):
    assert erwartet in correct(gesprochen, FACHBEGRIFFE)


@pytest.mark.parametrize("satz", [
    "das meeting ist morgen um zehn uhr im großen besprechungsraum",
    "bitte prüfe die rechnung und melde dich bei mir",
    "ich komme später, der zug hat verspätung",
    "wir treffen uns im kreis der kollegen",
    "die bilanz des jahres ist gut",
    "der gang ist zu eng",
])
def test_gewoehnliches_deutsch_bleibt_unberuehrt(satz):
    assert correct(satz, FACHBEGRIFFE) == satz


def test_bei_gleichem_code_gewinnt_der_aehnlichste():
    """„idempotent" ist ein eigener Begriff und darf nicht zu „Idempotenz" werden.

    Beide haben denselben phonetischen Code — entschieden wird ueber die
    Aehnlichkeit der Schreibweise.
    """
    assert "idempotent" in correct("das ist nicht idempotent", FACHBEGRIFFE)
    assert "Idempotenz" not in correct("das ist nicht idempotent", FACHBEGRIFFE)


def test_zu_kurze_begriffe_werden_ignoriert():
    """Kurze Begriffe kollidieren phonetisch mit zu vielem."""
    assert correct("wir nutzen api dafür", ["API"]) == "wir nutzen api dafür"


def test_ohne_begriffe_unveraendert():
    assert correct("beliebiger text", []) == "beliebiger text"


@pytest.mark.parametrize("text", ["", None])
def test_leere_eingabe(text):
    assert correct(text, FACHBEGRIFFE) == text


def test_kaputte_glossardatei_kostet_kein_diktat(tmp_path, monkeypatch):
    kaputt = tmp_path / "glossary.json"
    kaputt.write_text("{ das ist kein json")
    monkeypatch.setattr(glossary, "GLOSSARY_FILE", str(kaputt))
    assert glossary.load_terms() == []
    assert correct("beliebiger text") == "beliebiger text"


def test_fehlende_glossardatei(tmp_path, monkeypatch):
    monkeypatch.setattr(glossary, "GLOSSARY_FILE", str(tmp_path / "gibt_es_nicht.json"))
    assert glossary.load_terms() == []


def test_glossar_als_objekt_mit_terms(tmp_path, monkeypatch):
    datei = tmp_path / "glossary.json"
    datei.write_text(json.dumps({"terms": ["Kubernetes"]}), encoding="utf-8")
    monkeypatch.setattr(glossary, "GLOSSARY_FILE", str(datei))
    assert glossary.load_terms() == ["Kubernetes"]


# ─── Whisper-Prompt ───

def test_whisper_prompt_bleibt_unter_der_grenze():
    """Whisper nimmt nur die letzten ~224 Tokens. Ein zu langer Hinweis verliert
    seinen Anfang und ist dann so gut wie keiner (gemessen 8/10 statt 10/10)."""
    from voice_transcript.config import WHISPER_PROMPT_MAX_CHARS

    viele = [f"Sehr Langer Fachbegriff Nummer {i}" for i in range(200)]
    hinweis = whisper_prompt(viele)
    assert len(hinweis) <= WHISPER_PROMPT_MAX_CHARS


def test_whisper_prompt_behaelt_die_vorderen_begriffe():
    viele = FACHBEGRIFFE + [f"Fuellbegriff {i}" for i in range(200)]
    hinweis = whisper_prompt(viele)
    for begriff in FACHBEGRIFFE[:5]:
        assert begriff in hinweis


def test_whisper_prompt_ohne_begriffe_ist_leer():
    assert whisper_prompt([]) == ""
