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
