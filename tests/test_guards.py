"""Die Wächter, die verhindern, dass stillschweigend Text verloren geht.

Zwei davon sind echten Fehlern geschuldet: ein fester Token-Deckel hat lange
Diktate mitten im Satz abgeschnitten, und der Laengen-Waechter hat das nicht
gemerkt.
"""
import pytest

from voice_transcript.config import (
    MIN_LENGTH_RATIO,
    MLX_MAX_TOKENS,
    TOKEN_BUDGET_FACTOR,
    TOKEN_BUDGET_MARGIN,
)
from voice_transcript.llm_server import LLMServer, strip_thinking


class FakeTokenizer:
    """Ein Token je vier Zeichen — nahe an den gemessenen 4,10 fuer Deutsch."""

    def encode(self, text):
        return [0] * max(1, len(text) // 4)


@pytest.fixture
def server():
    s = LLMServer()
    s.tokenizer = FakeTokenizer()
    return s


# ─── Token-Budget ───

def test_budget_waechst_mit_der_eingabe(server):
    kurz = server._token_budget("a" * 100)
    lang = server._token_budget("a" * 4000)
    assert lang > kurz


def test_budget_hat_die_erwartete_form(server):
    text = "a" * 4000
    tokens = len(server.tokenizer.encode(text))
    erwartet = min(int(tokens * TOKEN_BUDGET_FACTOR) + TOKEN_BUDGET_MARGIN, MLX_MAX_TOKENS)
    assert server._token_budget(text) == erwartet


def test_budget_ueberschreitet_den_deckel_nie(server):
    assert server._token_budget("a" * 10_000_000) == MLX_MAX_TOKENS


def test_budget_laesst_der_ausgabe_luft(server):
    """Bereinigen macht den Text nicht kuerzer — Interpunktion und Absaetze kommen
    hinzu, also muss das Budget ueber der Eingabelaenge liegen."""
    text = "a" * 2000
    assert server._token_budget(text) > len(server.tokenizer.encode(text))


def test_deckel_reicht_fuer_lange_diktate():
    """Der alte Deckel von 1024 Tokens entsprach ~4.200 Zeichen — rund fuenf Minuten
    Sprechen. Genau da fing der stille Verlust an."""
    assert MLX_MAX_TOKENS >= 4096


# ─── Laengen-Waechter ───

@pytest.mark.parametrize("eingabe,ausgabe,verworfen", [
    ("a" * 1000, "a" * 900, False),   # normale Bereinigung
    ("a" * 1000, "a" * 1100, False),  # etwas laenger ist in Ordnung
    ("a" * 1000, "a" * 100, True),    # zusammengefasst statt bereinigt
    ("a" * 1000, "", True),           # leer
])
def test_waechter_greift_bei_zu_kurzer_ausgabe(eingabe, ausgabe, verworfen):
    zu_kurz = len(ausgabe) < len(eingabe) * MIN_LENGTH_RATIO
    assert zu_kurz == verworfen


def test_alter_waechter_haette_abschneiden_durchgewinkt():
    """Der Beleg fuer den behobenen Fehler: 6.000 Zeichen Eingabe, bei 1024 Tokens
    auf ~4.200 Zeichen abgeschnitten — 4200 > 1800, also durchgewinkt."""
    eingabe, abgeschnitten = 6000, 4200
    assert abgeschnitten > eingabe * MIN_LENGTH_RATIO


# ─── <think>-Blöcke ───

@pytest.mark.parametrize("roh,erwartet", [
    ("<think>ueberlege</think>Das Ergebnis.", "Das Ergebnis."),
    ("<think>a\nb\nc</think>  Text  ", "Text"),
    ("Kein Think-Block.", "Kein Think-Block."),
    ("<think>nur denken</think>", ""),
])
def test_think_bloecke_werden_entfernt(roh, erwartet):
    assert strip_thinking(roh) == erwartet
