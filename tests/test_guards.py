"""Die Wächter, die verhindern, dass stillschweigend Text verloren geht.

Zwei davon sind echten Fehlern geschuldet: ein fester Token-Deckel hat lange
Diktate mitten im Satz abgeschnitten, und der Laengen-Waechter hat das nicht
gemerkt.
"""
import numpy as np
import pytest

from voice_transcript.config import (
    MIN_LENGTH_RATIO,
    MLX_MAX_TOKENS,
    TOKEN_BUDGET_FACTOR,
    TOKEN_BUDGET_MARGIN,
    WHISPER_MAX_OVERHANG,
    WHISPER_MIN_VOICED,
)
from voice_transcript.llm_server import LLMServer, drop_invented_ending, strip_thinking


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


# ─── Prompt-Markierungen ───
#
# Der System-Prompt klammert das Diktat in <transkript>…</transkript>. Bei langen
# Diktaten hat das Modell die Klammer mit ausgegeben, und sie landete im Editor.
# Die Regeln dafuer stehen in test_cleanup.py — hier zaehlt nur, dass generate()
# sie tatsaechlich anwendet und der Laengen-Waechter danach greift.

class FakeAntwort:
    def __init__(self, text, finish_reason=None):
        self.text = text
        self.finish_reason = finish_reason
        self.generation_tokens = 1


def _server_mit_ausgabe(monkeypatch, ausgabe):
    """Ein Server, dessen „Modell" genau `ausgabe` erzeugt."""
    import sys
    import types

    modul = types.ModuleType("mlx_lm")
    modul.stream_generate = lambda *a, **kw: [FakeAntwort(ausgabe, "stop")]
    monkeypatch.setitem(sys.modules, "mlx_lm", modul)

    s = LLMServer()
    s.tokenizer = FakeTokenizer()
    s.tokenizer.apply_chat_template = lambda *a, **kw: "prompt"
    s.model = object()  # verhindert das Nachladen in load_model()
    s.sampler = object()
    return s


def test_generate_gibt_keine_markierungen_heraus(monkeypatch):
    text = "Der Bilanzkreis für das Netzentgelt stimmt nicht."
    server = _server_mit_ausgabe(monkeypatch, f"<transkript>{text}</transkript>")

    ergebnis, hinweis = server.generate(text)

    assert ergebnis == text
    assert hinweis is None


def test_generate_nimmt_den_rohtext_wenn_nur_markierungen_kommen(monkeypatch):
    """Eine Ausgabe aus lauter Markierungen ist kein Ergebnis — dann muss der
    unbereinigte Text gewinnen, statt dass „<transkript></transkript>" im Editor
    landet."""
    text = "Der Bilanzkreis für das Netzentgelt stimmt nicht."
    server = _server_mit_ausgabe(monkeypatch, "<transkript></transkript>")

    ergebnis, hinweis = server.generate(text)

    assert ergebnis == text
    assert hinweis == "LLM lieferte kein Ergebnis"


# ─── Der Rohtext-Pfad ───

def test_verworfene_bereinigung_laesst_das_diktat_unangetastet():
    """Gibt der Server den Rohtext zurueck, darf der Client ihn nicht saeubern.

    Der Befund aus dem Review: `_saeubern` lief auf allem, was zurueckkam — auch
    auf dem unbereinigten Diktat, das der Server bei verworfener Bereinigung
    liefert. Aus einer diktierten Ueberschrift wurde so stillschweigend Fliesstext.
    """
    from voice_transcript.llm import _saeubern

    diktat = "Transkript: Meeting vom 3. Maerz mit Anna."
    assert _saeubern(diktat, diktat) == diktat


# ─── Erfundener Schluss ───
#
# Im letzten 30-s-Fenster mit kaum noch Sprache erfindet Whisper einen Schluss. Die
# Zeiten unten stammen aus echten Laeufen. Weil der Filter Text wegnimmt, steht hier
# vor allem fest, was er *nicht* anfassen darf.

RATE = 16000


@pytest.fixture(autouse=True)
def _ohne_app_log(monkeypatch):
    """Jedes Verwerfen wird protokolliert — aber nicht ins echte app.log, sonst
    stuende dort nach jedem Testlauf ein erfundenes „Vielen Dank."."""
    monkeypatch.setattr("voice_transcript.llm_server.log", lambda *a, **kw: None)


def _ergebnis(*segmente):
    """Ein Whisper-Ergebnis aus (start, ende, text) — der Gesamttext ist wie bei
    Whisper die Segmente hintereinander."""
    segs = [{"start": a, "end": b, "text": t} for a, b, t in segmente]
    return {"text": "".join(s["text"] for s in segs), "segments": segs}


def _aufnahme(dauer, *sprache, pegel=0.1, rauschen=0.003):
    """Raumrauschen ueber `dauer` Sekunden, darin Sprache in den Spannen (von, bis)."""
    rng = np.random.default_rng(0)
    samples = rng.normal(0, rauschen, int(dauer * RATE)).astype(np.float32)
    for von, bis in sprache:
        a, b = int(von * RATE), int(bis * RATE)
        samples[a:b] += rng.normal(0, pegel, b - a).astype(np.float32)
    return samples


def test_erfundener_schluss_hinter_der_aufnahme_faellt_weg():
    ergebnis = _ergebnis(
        (62.4, 83.4, " Rückfragen sollen interaktiver sein."),
        (83.4, 113.4, " Vielen Dank."),
    )
    samples = _aufnahme(85.8, (62.4, 83.5))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Rückfragen sollen interaktiver sein."


def test_erfundener_schluss_ohne_zeitstempel_faellt_weg():
    """Ohne Zeitstempel setzt Whisper das Ende auf das Aufnahmeende — kein
    Ueberhang. Hier greift nur die Pegelpruefung."""
    ergebnis = _ergebnis(
        (62.4, 83.4, " Rückfragen sollen interaktiver sein."),
        (83.4, 85.8, " Frau, Frau, Frau, Frau, Frau, Frau,"),
    )
    samples = _aufnahme(85.8, (62.4, 83.5))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Rückfragen sollen interaktiver sein."


def test_mehrere_erfundene_segmente_fallen_alle_weg():
    ergebnis = _ergebnis(
        (50.0, 72.7, " Das müssen wir bitte noch fixen."),
        (72.7, 74.0, " Auf Wiedersehen."),
        (74.0, 77.8, " Auf Wiedersehen."),
    )
    samples = _aufnahme(77.8, (50.0, 72.8))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Das müssen wir bitte noch fixen."


def test_mitten_im_wort_gestoppt_bleibt_der_text():
    """Gestoppt im letzten Wort ragt das echte Segment etwas ueber das Ende —
    gemessen bis 0,6 s. Das darf nicht verloren gehen."""
    ergebnis = _ergebnis((0.0, 24.0, " Ich möchte, dass wir das Notion-Repo erweitern."))
    samples = _aufnahme(23.4, (0.2, 23.4))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Ich möchte, dass wir das Notion-Repo erweitern."


def test_gesagtes_vielen_dank_bleibt_nur_die_wiederholung_faellt():
    """Gemessen: nach einem echten „Vielen Dank." haengt Whisper ein zweites an."""
    ergebnis = _ergebnis(
        (60.0, 67.5, " Kannst du das bitte einmal checken?"),
        (67.5, 68.2, " Vielen Dank."),
        (68.2, 98.1, " Vielen Dank."),
    )
    samples = _aufnahme(68.6, (60.0, 67.2), (67.5, 68.2))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Kannst du das bitte einmal checken? Vielen Dank."


def test_kurzes_leises_ja_am_ende_bleibt():
    ergebnis = _ergebnis(
        (0.0, 12.0, " Passt das so?"),
        (12.5, 13.0, " Ja."),
    )
    samples = _aufnahme(13.2, (0.3, 12.0), (12.55, 12.95), pegel=0.1)
    samples[int(12.55 * RATE):int(12.95 * RATE)] *= 0.4  # leiser als der Rest
    assert drop_invented_ending(ergebnis, samples, RATE) == "Passt das so? Ja."


def test_leiser_sprecher_gilt_nicht_als_stille():
    """Der Pegel zaehlt relativ zur eigenen Aufnahme — gemessen mit Faktor 0,15."""
    ergebnis = _ergebnis(
        (0.0, 20.0, " Das müssen wir bitte noch fixen."),
        (20.0, 23.0, " remains remains remains remains"),
    )
    samples = _aufnahme(23.0, (0.3, 20.1), pegel=0.015, rauschen=0.0008)
    assert drop_invented_ending(ergebnis, samples, RATE) == "Das müssen wir bitte noch fixen."


def test_mittendrin_wird_nie_verworfen():
    """Nur der Schluss wird geprueft. Ein Segment ohne Pegel *zwischen* echten
    bleibt — dort waere ein Irrtum ein Loch mitten im Diktat."""
    ergebnis = _ergebnis(
        (0.0, 10.0, " Erster Teil."),
        (10.0, 14.0, " Unklar."),
        (14.0, 20.0, " Zweiter Teil."),
    )
    samples = _aufnahme(20.5, (0.3, 10.0), (14.0, 20.0))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Erster Teil. Unklar. Zweiter Teil."


def test_lauter_raum_laesst_stehen_der_zeitstempel_greift_weiter():
    """Ist der Raum so laut wie die Stimme, kann der Pegel nichts unterscheiden und
    laesst stehen. Ein Segment hinter der Aufnahme faellt trotzdem."""
    samples = _aufnahme(85.8, (62.4, 83.5), pegel=0.1, rauschen=0.1)

    ohne_zeitstempel = _ergebnis((62.4, 83.4, " Satz."), (83.4, 85.8, " Frau, Frau,"))
    assert drop_invented_ending(ohne_zeitstempel, samples, RATE) == "Satz. Frau, Frau,"

    dahinter = _ergebnis((62.4, 83.4, " Satz."), (83.4, 113.4, " Vielen Dank."))
    assert drop_invented_ending(dahinter, samples, RATE) == "Satz."


def test_ohne_befund_bleibt_whispers_text_unveraendert():
    ergebnis = {"text": "  Genau so, wie Whisper ihn liefert. ", "segments": [
        {"start": 0.0, "end": 3.0, "text": " Genau so, wie Whisper ihn liefert."},
    ]}
    samples = _aufnahme(3.4, (0.2, 3.0))
    assert drop_invented_ending(ergebnis, samples, RATE) == "Genau so, wie Whisper ihn liefert."


def test_nur_stille_ergibt_leeren_text():
    """Leer heisst fuer asr.py „Whisper nicht nutzbar" — dann bekommt yap dieselbe
    Aufnahme, statt dass ein erfundenes „Vielen Dank." im Editor landet."""
    ergebnis = _ergebnis((0.0, 30.0, " Vielen Dank."))
    assert drop_invented_ending(ergebnis, np.zeros(4 * RATE, np.float32), RATE) == ""


@pytest.mark.parametrize("ueberhang,bleibt", [
    (WHISPER_MAX_OVERHANG, True),
    (WHISPER_MAX_OVERHANG + 0.1, False),
])
def test_grenze_des_ueberhangs(ueberhang, bleibt):
    ergebnis = _ergebnis((0.0, 10.0 + ueberhang, " Satz."))
    samples = _aufnahme(10.0, (0.2, 10.0))
    assert (drop_invented_ending(ergebnis, samples, RATE) == "Satz.") == bleibt


def test_grenzen_liegen_zwischen_den_messwerten():
    """Gemessen: echte Segmente ragten hoechstens 0,6 s ueber das Ende, erfundene
    mindestens 3,6 s. Echte Endsegmente enthielten mindestens 0,48 s Sprache,
    erfundene hoechstens 0,09 s. Beide Grenzen muessen dazwischen liegen."""
    assert 0.6 < WHISPER_MAX_OVERHANG < 3.6
    assert 0.09 < WHISPER_MIN_VOICED < 0.48


def test_ergebnis_ohne_segmente():
    samples = _aufnahme(5.0, (0.2, 4.0))
    assert drop_invented_ending({"text": " Nur Text. "}, samples, RATE) == "Nur Text."
