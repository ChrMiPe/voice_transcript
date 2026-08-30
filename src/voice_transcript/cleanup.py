"""Regex-Bereinigung — bewusst auf das Unstrittige beschraenkt.

Hier stand einmal eine Liste mit „also", „halt", „ja", „eigentlich", „eben",
„quasi", „sozusagen", „irgendwie" und „nicht wahr". Kontextblind entfernt richtet
das Schaden an:

    „das ist nicht wahr"                      -> „das ist"
    „ich halt das fuer richtig"               -> „ich das fuer richtig"
    „ja das machen wir so"                    -> „das machen wir so"
    „eigentlich hatte ich etwas anderes vor"  -> „hatte ich etwas anderes vor"

Und weil der Filter *vor* dem LLM lief, konnte das Modell nichts davon retten — es
hat den beschaedigten Satz zu fluessigem, falschem Deutsch poliert.

Ob „also" Fuellwort oder Konjunktion ist, entscheidet der Kontext. Das kann ein
Regex prinzipiell nicht und das Modell sehr wohl, deshalb steht die Aufgabe jetzt
im System-Prompt. Uebrig bleiben hier Laute, die in geschriebenem Deutsch nie ein
Wort sind — die duerfen bedenkenlos weg. Das hat auch dann Wert, wenn das LLM
ausfaellt: der Rueckfall-Text ist damit lesbar statt voller „ähm".
"""
import re

# Nur echte Verzoegerungslaute. Nichts, was auch Bedeutung tragen koennte —
# „mhm" etwa heisst „ja" und gehoert deshalb nicht hierher.
DISFLUENCIES = [
    r"\bäh+m?\b",
    r"\böh+m?\b",
    r"\bhm+\b",
]


def clean_german_text(text):
    if not text:
        return ""

    for pattern in DISFLUENCIES:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Nach dem Entfernen bleiben Luecken zurueck: doppelte Leerzeichen und
    # Leerzeichen vor Satzzeichen.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?]) *\1+", r"\1", text)
    text = re.sub(r"\n +", "\n", text)

    return text.strip()


# ─── Nachbereitung der Modellausgabe ───
#
# Der System-Prompt klammert das Diktat in <transkript>…</transkript>. Die Klammer
# hat einen Grund — sie trennt Text von Anweisung, damit ein diktiertes „schreib
# eine Mail" nicht als Auftrag ans Modell durchschlaegt. Der Preis: ein 4B-Modell
# gibt die Klammer gelegentlich mit aus, und dann steht sie vor und hinter dem
# Text im Editor. Beobachtet vor allem bei langen Diktaten — je weiter die
# oeffnende Markierung zurueckliegt, desto eher reproduziert das Modell sie mit.
#
# Der Prompt allein kann das nicht verhindern: er *bittet* um reinen Text, mehr
# nicht. Verlaesslich wird es erst, wenn die Markierungen nach der Generierung
# entfernt werden — egal was das Modell ausgibt.

# Steht die Markierung allein auf einer Zeile, faellt die Zeile mit weg. Sonst
# bliebe ein Absatzumbruch zurueck, wo vorher nur ein Zeilenumbruch war.
_MARKIERUNG_ZEILE = re.compile(
    r"^[ \t]*</?[ \t]*transkript[ \t]*>[ \t]*\n?", re.IGNORECASE | re.MULTILINE
)
# Sonst nur die Markierung selbst, samt der Leerzeichen daneben. Die werden zu
# genau einem zusammengezogen, wenn auf beiden Seiten welche standen — „Wort
# <transkript> Wort" darf weder „WortWort" noch „Wort  Wort" ergeben. Eine
# globale Leerraum-Glaettung waere hier falsch: sie liefe auch auf Ausgaben ohne
# jede Markierung und ebnete dort Einrueckung und gesetzte Doppelabstaende ein.
_MARKIERUNG = re.compile(
    r"(?P<vor>[ \t]*)</?[ \t]*transkript[ \t]*>(?P<nach>[ \t]*)", re.IGNORECASE
)

# Ohne spitze Klammern ist „Transkript" ein ganz normales Wort — als Ueberschrift
# („Transkript: Meeting vom 3. Maerz"), als Listenpunkt, am Satzende. Entfernt
# wird es deshalb nur, wenn es *an beiden Enden zugleich* steht: genau das
# gemeldete Symptom, und keine Formulierung, die beim Diktieren entsteht.
_BLANK_ANFANG = re.compile(r"\A[ \t]*transkript[ \t]*(?::[ \t]*|\n)", re.IGNORECASE)
_BLANK_ENDE = re.compile(
    r"(?:\n[ \t]*|(?<=[.!?])[ \t]*)transkript[ \t]*:?[ \t]*\Z", re.IGNORECASE
)


def _fuege_zusammen(treffer):
    """Ersatz fuer eine entfernte Markierung: ein Leerzeichen oder keins."""
    return " " if treffer.group("vor") and treffer.group("nach") else ""


def _ohne_blanke_markierung(text):
    """Entfernt „Transkript" ohne Klammern — nur wenn es beide Enden einfasst."""
    anfang = _BLANK_ANFANG.search(text)
    if anfang is None:
        return text

    ende = _BLANK_ENDE.search(text)
    if ende is None:
        return text

    # Ueberlappen sie, bestand die Ausgabe aus nichts als der Klammer.
    if anfang.end() > ende.start():
        return ""

    return text[anfang.end():ende.start()]


def strip_prompt_markers(text):
    """Entfernt die Prompt-Markierungen aus einer Modellausgabe."""
    if not text:
        return ""

    ohne = _MARKIERUNG_ZEILE.sub("", text)
    ohne = _MARKIERUNG.sub(_fuege_zusammen, ohne)
    ohne = _ohne_blanke_markierung(ohne)

    return ohne.strip()
