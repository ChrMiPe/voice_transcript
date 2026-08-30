"""Das Aufraeumen beim Beenden darf nicht auf halbem Weg steckenbleiben.

Zwei Wege fuehren hinein: die AppKit-Benachrichtigung (der Menueeintrag „Beenden",
das Abmelden) und atexit (Ctrl+C aus dem Quellbaum). Der zweite ist die
Rueckversicherung fuer den ersten — und war es nicht mehr, als das
„schon erledigt"-Flag noch *vor* der Arbeit gesetzt wurde.
"""
import pytest

from voice_transcript import menubar
from voice_transcript.llm_server import _entfernen


class DummyApp:
    """Nur die Felder, die _cleanup anfasst."""
    _cleaned_up = False
    _llm_process = None


def test_abgebrochenes_aufraeumen_wird_nachgeholt(monkeypatch):
    """Scheitert der erste Weg, muss der zweite noch etwas zu tun finden.

    Der Befund aus dem Review: llm_stop_server() kann mit FileNotFoundError
    herausfliegen (Wettlauf mit dem Server, der dieselben Dateien wegraeumt). Der
    Observer schluckt den Fehler — und weil das Flag schon stand, war auch atexit
    stillgelegt. Die PID-Datei blieb liegen, und _is_already_running() hielt die
    App danach fuer laufend: der naechste Start brach wortlos ab.
    """
    versuche = []

    def stop_der_erst_scheitert():
        versuche.append(1)
        if len(versuche) == 1:
            raise FileNotFoundError("Wettlauf mit dem Server")

    monkeypatch.setattr(menubar, "llm_stop_server", stop_der_erst_scheitert)
    monkeypatch.setattr(menubar.os.path, "exists", lambda _: False)

    app = DummyApp()

    with pytest.raises(FileNotFoundError):
        menubar.VoiceTranscriptApp._cleanup(app)
    assert app._cleaned_up is False, "nach einem Abbruch darf das Flag nicht stehen"

    menubar.VoiceTranscriptApp._cleanup(app)
    assert app._cleaned_up is True
    assert len(versuche) == 2, "der zweite Weg muss es erneut versucht haben"


def test_aufraeumen_laeuft_nur_einmal(monkeypatch):
    """Der Normalfall: aus dem Quellbaum feuern beide Wege, die Arbeit faellt einmal an."""
    aufrufe = []
    monkeypatch.setattr(menubar, "llm_stop_server", lambda: aufrufe.append(1))
    monkeypatch.setattr(menubar.os.path, "exists", lambda _: False)

    app = DummyApp()
    menubar.VoiceTranscriptApp._cleanup(app)
    menubar.VoiceTranscriptApp._cleanup(app)

    assert len(aufrufe) == 1


def test_entfernen_vertraegt_fehlende_dateien(tmp_path):
    """exists() und remove() sind zwei Schritte — dazwischen passt der Server."""
    da = tmp_path / "da.pid"
    da.write_text("1")

    _entfernen(str(da), str(tmp_path / "schon-weg.sock"))

    assert not da.exists()
