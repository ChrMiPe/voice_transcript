"""Fachbegriffe erkennen und richtig schreiben.

Die Spracherkennung kennt kein Fachvokabular: aus „Kubernetes" wird „Kuberneetes",
aus „Idempotenz" „Iddempotenz". Das LLM kann das nur raten, weil es den gemeinten
Begriff nicht kennt.

Statt jede Verhoerung einzeln zu pflegen, wird phonetisch verglichen: die Koelner
Phonetik ist das deutsche Gegenstueck zu Soundex und gibt aehnlich klingenden
Woertern denselben Code. „Kuberneetes" und „Kubernetes" fallen damit zusammen —
gepflegt wird eine Begriffsliste, keine Fehlerliste.

Dieselbe Liste geht zusaetzlich in den System-Prompt, damit das Modell im Kontext
nachziehen kann, was der phonetische Abgleich nicht erwischt.
"""
import json
import os
import re
from difflib import SequenceMatcher

from voice_transcript.applog import log
from voice_transcript.config import (
    GLOSSARY_FILE,
    GLOSSARY_MAX_WORDS,
    GLOSSARY_MIN_CHARS,
    GLOSSARY_MIN_SIMILARITY,
    GLOSSARY_PROMPT_MAX,
)

_UMLAUTE = {
    "ä": "a", "ö": "o", "ü": "u", "ß": "ss",
    "à": "a", "á": "a", "â": "a", "é": "e", "è": "e", "ê": "e",
    "í": "i", "ì": "i", "ó": "o", "ò": "o", "ú": "u", "ù": "u",
}

_WORT = re.compile(r"[^\W\d_]+", re.UNICODE)


def _normalisieren(wort):
    wort = wort.lower()
    for quelle, ziel in _UMLAUTE.items():
        wort = wort.replace(quelle, ziel)
    return "".join(c for c in wort if "a" <= c <= "z")


def koelner_phonetik(wort):
    """Koelner Phonetik nach Postel (1969). Leerer String, wenn nichts codierbar ist.

    Beispiele: Müller -> 657, Wikipedia -> 3412, Breschnew -> 17863.
    """
    wort = _normalisieren(wort)
    if not wort:
        return ""

    ziffern = []
    laenge = len(wort)
    for i, zeichen in enumerate(wort):
        davor = wort[i - 1] if i > 0 else ""
        danach = wort[i + 1] if i + 1 < laenge else ""

        if zeichen in "aeijouy":
            code = "0"
        elif zeichen == "h":
            code = ""  # H bekommt keinen Code, beeinflusst aber die Nachbarn
        elif zeichen == "b":
            code = "1"
        elif zeichen == "p":
            code = "3" if danach == "h" else "1"
        elif zeichen in "dt":
            code = "8" if danach in "csz" else "2"
        elif zeichen in "fvw":
            code = "3"
        elif zeichen in "gkq":
            code = "4"
        elif zeichen == "c":
            if i == 0:
                code = "4" if danach in "ahkloqrux" else "8"
            elif davor in "sz":
                code = "8"
            else:
                code = "4" if danach in "ahkoqux" else "8"
        elif zeichen == "x":
            code = "8" if davor in "ckq" else "48"
        elif zeichen == "l":
            code = "5"
        elif zeichen in "mn":
            code = "6"
        elif zeichen == "r":
            code = "7"
        elif zeichen in "sz":
            code = "8"
        else:
            code = ""

        ziffern.append(code)

    folge = "".join(ziffern)

    # Gleiche Ziffern hintereinander zusammenfassen
    entdoppelt = []
    for ziffer in folge:
        if not entdoppelt or entdoppelt[-1] != ziffer:
            entdoppelt.append(ziffer)

    if not entdoppelt:
        return ""

    # Nullen fallen weg — ausser einer am Anfang
    return entdoppelt[0] + "".join(z for z in entdoppelt[1:] if z != "0")


def load_terms():
    """Begriffsliste aus der Konfiguration. Fehler duerfen kein Diktat kosten."""
    if not os.path.exists(GLOSSARY_FILE):
        return []
    try:
        with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
            daten = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Glossar nicht lesbar: {type(e).__name__}: {e}")
        return []

    # Sowohl ["Kubernetes", ...] als auch {"terms": [...]} annehmen.
    if isinstance(daten, dict):
        daten = daten.get("terms", [])
    if not isinstance(daten, list):
        log("Glossar hat unerwartetes Format — erwartet wird eine Liste von Begriffen")
        return []

    return [t.strip() for t in daten if isinstance(t, str) and t.strip()]


def _index(terms):
    """Phonetischer Index: Code -> alle Begriffe mit diesem Code.

    Der Code entsteht aus dem Begriff *ohne* Leerzeichen. Das ist der Kniff, mit
    dem beide Richtungen funktionieren: „Active Directory" wird als zwei Woerter
    gesprochen und als zwei erkannt, „Bilanzkreis" ist ein Wort, wird aber gern als
    „bilanz kreis" erkannt. Wuerde nach Wortanzahl gruppiert, faende man jeweils nur
    eine der beiden Faelle.

    Zu kurze Begriffe bleiben draussen: die Koelner Phonetik ist grob, und bei drei
    Buchstaben kollidiert ein Begriff mit halb Deutschland.

    Mehrere Begriffe koennen denselben Code haben („Idempotenz" und „idempotent").
    Deshalb eine Liste — welcher gemeint ist, entscheidet spaeter die Aehnlichkeit
    der Schreibweise.
    """
    index = {}
    for term in terms:
        if len(_normalisieren(term)) < GLOSSARY_MIN_CHARS:
            continue
        code = koelner_phonetik(term)
        if not code:
            continue
        index.setdefault(code, []).append(term)
    return index


def _bester_treffer(kandidat, terms):
    """Der aehnlichste Begriff aus der Kandidatenliste, oder None.

    Der phonetische Code fasst grosszuegig zusammen; die Schreibweise muss
    zusaetzlich aehnlich sein, sonst wird aus einem Zufallstreffer ein Fachbegriff,
    den niemand gesagt hat.
    """
    normalisiert = _normalisieren(kandidat)
    bester, bestwert = None, 0.0
    for term in terms:
        wert = SequenceMatcher(None, normalisiert, _normalisieren(term)).ratio()
        if wert > bestwert:
            bester, bestwert = term, wert
    if bestwert < GLOSSARY_MIN_SIMILARITY:
        return None
    return bester


def correct(text, terms=None):
    """Schreibt phonetisch passende Stellen auf die Glossar-Begriffe um."""
    if not text:
        return text

    terms = load_terms() if terms is None else terms
    if not terms:
        return text

    index = _index(terms)
    if not index:
        return text

    treffer = [(m.start(), m.end(), m.group()) for m in _WORT.finditer(text)]
    if not treffer:
        return text

    ersetzungen = []
    verbraucht = set()

    # Laengere Wortgruppen zuerst: sonst verbraucht „bilanz" allein die Stelle,
    # bevor „bilanz kreis" ueberhaupt geprueft wird.
    for anzahl in range(GLOSSARY_MAX_WORDS, 0, -1):
        for i in range(len(treffer) - anzahl + 1):
            if any(j in verbraucht for j in range(i, i + anzahl)):
                continue

            gruppe = treffer[i:i + anzahl]
            # Nur ueber Leerzeichen hinweg zusammenfassen — Satzzeichen dazwischen
            # bedeuten, dass es kein zusammenhaengender Begriff ist.
            if any(
                text[gruppe[k][1]:gruppe[k + 1][0]].strip()
                for k in range(anzahl - 1)
            ):
                continue

            kandidat = " ".join(w for _, _, w in gruppe)
            term = _bester_treffer(kandidat, index.get(koelner_phonetik(kandidat), []))
            if term is None:
                continue

            if kandidat != term:
                ersetzungen.append((gruppe[0][0], gruppe[-1][1], term))
            verbraucht.update(range(i, i + anzahl))

    if not ersetzungen:
        return text

    # Von hinten ersetzen, damit die Positionen davor gueltig bleiben.
    for start, ende, term in sorted(ersetzungen, reverse=True):
        log(f"Glossar: „{text[start:ende]}“ -> „{term}“")
        text = text[:start] + term + text[ende:]

    return text


def whisper_prompt(terms=None):
    """Vokabular-Hinweis fuer Whispers initial_prompt.

    Whisper konditioniert die Dekodierung auf diesen Text — deshalb eine schlichte
    Aufzaehlung und keine Anweisung: das Modell setzt hier keine Regeln um, es
    erwartet nur, solche Woerter zu hoeren. Gemessen hebt das die Trefferquote von
    8/10 auf 10/10 Fachbegriffe, ohne messbaren Zeitaufwand.
    """
    terms = load_terms() if terms is None else terms
    if not terms:
        return ""
    return "Fachbegriffe: " + ", ".join(terms[:GLOSSARY_PROMPT_MAX]) + "."


def prompt_section(terms=None):
    """Glossar-Absatz fuer den System-Prompt, oder leerer String ohne Begriffe."""
    terms = load_terms() if terms is None else terms
    if not terms:
        return ""

    auswahl = terms[:GLOSSARY_PROMPT_MAX]
    return (
        "\n\nFachbegriffe, die vorkommen koennen:\n"
        + ", ".join(auswahl)
        + "\nKorrigiere ihre Schreibweise, wenn sie erkennbar gemeint sind. Setze "
        "KEINEN dieser Begriffe ein, wenn er nicht gesagt wurde."
    )
