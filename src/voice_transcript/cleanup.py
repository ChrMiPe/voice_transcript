import re

FILLER_WORDS = [
    r"\bähm?\b", r"\böhm?\b", r"\bhm+\b",
    r"\balso\b", r"\bhalt\b", r"\bsozusagen\b", r"\bquasi\b",
    r"\birgendwie\b", r"\beigentlich\b", r"\beben\b",
    r"\bja\b", r"\bne\b", r"\bgell\b", r"\bnicht wahr\b",
]


def clean_german_text(text):
    if not text:
        return ""

    # Füllwörter entfernen
    for pattern in FILLER_WORDS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Whitespace normalisieren
    text = re.sub(r" +", " ", text)
    text = text.strip()

    return text
