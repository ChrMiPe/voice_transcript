"""Der Fuellwort-Filter darf keine Bedeutung mehr zerstoeren.

Frueher entfernte cleanup.py auch „also", „halt", „ja", „eigentlich", „eben",
„quasi" und „nicht wahr" — kontextblind, und *vor* dem LLM, das nichts davon retten
konnte. Diese Tests halten fest, dass nur echte Verzoegerungslaute weichen.
"""
import pytest

from voice_transcript.cleanup import clean_german_text, strip_prompt_markers


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


# ─── Prompt-Markierungen in der Modellausgabe ───
#
# Der System-Prompt klammert das Diktat in <transkript>…</transkript>. Das Modell
# hat die Klammer bei langen Diktaten mit ausgegeben, und dann stand sie vor und
# hinter dem Text im Editor. Diese Tests halten fest, dass sie verschwindet — und
# dass „Transkript" als normales Wort stehen bleibt.

@pytest.mark.parametrize("ausgabe,erwartet", [
    ("<transkript>Der Bilanzkreis stimmt nicht.</transkript>",
     "Der Bilanzkreis stimmt nicht."),
    ("<transkript>Der Bilanzkreis stimmt nicht.",
     "Der Bilanzkreis stimmt nicht."),
    ("Der Bilanzkreis stimmt nicht.</transkript>",
     "Der Bilanzkreis stimmt nicht."),
    ("< transkript >Der Bilanzkreis stimmt nicht.</ TRANSKRIPT >",
     "Der Bilanzkreis stimmt nicht."),
    # Ohne spitze Klammern, allein auf einer Zeile
    ("Transkript\nDer Bilanzkreis stimmt nicht.\nTranskript",
     "Der Bilanzkreis stimmt nicht."),
    ("Transkript:\nDer Bilanzkreis stimmt nicht.\nTranskript:",
     "Der Bilanzkreis stimmt nicht."),
    # Als Anrede am Anfang, angehaengt am Ende
    ("Transkript: Der Bilanzkreis stimmt nicht. Transkript",
     "Der Bilanzkreis stimmt nicht."),
    # Absaetze ueberleben
    ("<transkript>Erster Absatz.\n\nZweiter Absatz.</transkript>",
     "Erster Absatz.\n\nZweiter Absatz."),
])
def test_markierungen_verschwinden(ausgabe, erwartet):
    assert strip_prompt_markers(ausgabe) == erwartet


@pytest.mark.parametrize("ausgabe", [
    # „Transkript" als bedeutungstragendes Wort — mitten im Satz, am Satzende
    "Ich schicke dir das Transkript von gestern.",
    "Kannst du mir bitte das Transkript schicken?",
    "Das Transkript ist fertig.",
    "Transkript ist ein schwieriges Wort.",
])
def test_wort_transkript_bleibt(ausgabe):
    assert strip_prompt_markers(ausgabe) == ausgabe


def test_reine_markierung_wird_leer():
    """Eine Ausgabe aus lauter Markierungen ist kein Ergebnis.

    Wichtig, weil der Aufrufer an genau diesem „leer" erkennt, dass er den
    unbereinigten Text nehmen muss — sonst landete „<transkript></transkript>"
    als Diktat im Editor.
    """
    assert strip_prompt_markers("<transkript></transkript>") == ""
    assert strip_prompt_markers("") == ""
    assert strip_prompt_markers(None) == ""


# ─── Die Befunde des Reviews ───
#
# Die erste Fassung entfernte „Transkript" ohne Klammern auch dann, wenn es nur an
# *einem* Ende stand — und ebnete bei jeder Ausgabe Leerraum ein, auch ohne jede
# Markierung. Beides traf echtes Diktat.

@pytest.mark.parametrize("ausgabe", [
    # Ueberschrift: steht nur am Anfang, also keine Klammer
    "Transkript: Meeting vom 3. Maerz mit Anna.",
    # Listenpunkt: allein auf einer Zeile, aber mitten im Text
    "Aufgaben:\nTranskript\nProtokoll",
    # Am Satzende, ohne Gegenstueck am Anfang
    "Fertig. Transkript",
    # Leerraum, den niemand angefasst hat: Einrueckung und gesetzte Abstaende
    "- Punkt eins\n  - Unterpunkt",
    'Er sagte:  "Ja"  und ging.',
    "Erste Zeile.\nZweite Zeile.\n\n\nVierte Zeile.",
])
def test_einseitiges_transkript_und_leerraum_bleiben(ausgabe):
    assert strip_prompt_markers(ausgabe) == ausgabe


@pytest.mark.parametrize("ausgabe,erwartet", [
    # Klammer allein auf einer Zeile nimmt die Zeile mit — sonst entstuende ein
    # Absatz, wo vorher nur ein Zeilenumbruch war.
    ("Absatz eins.\n<transkript>\nAbsatz zwei.", "Absatz eins.\nAbsatz zwei."),
    # Mitten im Satz bleibt genau ein Leerzeichen stehen, nicht keins und nicht zwei
    ("Wort <transkript> Wort", "Wort Wort"),
    ("Wort<transkript>Wort", "WortWort"),
])
def test_klammer_hinterlaesst_keine_luecke(ausgabe, erwartet):
    assert strip_prompt_markers(ausgabe) == erwartet
