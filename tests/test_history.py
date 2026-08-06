"""Historie und Verlaufsdatei duerfen die App nicht am Start hindern."""
import json

import pytest

from voice_transcript import main


@pytest.fixture
def mit_historie(tmp_path, monkeypatch):
    def setzen(inhalt):
        datei = tmp_path / "history.json"
        datei.write_text(inhalt, encoding="utf-8")
        monkeypatch.setattr(main, "HISTORY_FILE", str(datei))
        return datei
    return setzen


def test_gueltige_historie(mit_historie):
    mit_historie(json.dumps([{"raw": "a", "result": "A", "timestamp": "2026-01-01T10:00:00"}]))
    eintraege = main.load_history()
    assert len(eintraege) == 1 and eintraege[0]["result"] == "A"


def test_kaputte_datei_verhindert_den_start_nicht(mit_historie):
    """_refresh_history() laeuft in __init__ — eine Ausnahme hier hiess: App tot."""
    mit_historie("{ kein json")
    assert main.load_history() == []


def test_falsches_format(mit_historie):
    mit_historie(json.dumps({"kein": "array"}))
    assert main.load_history() == []


def test_eintraege_ohne_result_werden_verworfen(mit_historie):
    """Menue und Panel greifen auf entry["result"] zu."""
    mit_historie(json.dumps([{"raw": "a"}, {"result": "B"}, "kein dict"]))
    eintraege = main.load_history()
    assert [e["result"] for e in eintraege] == ["B"]


def test_fehlende_datei(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "HISTORY_FILE", str(tmp_path / "fehlt.json"))
    assert main.load_history() == []


def test_speichern_ueberlebt_kaputte_datei(mit_historie):
    datei = mit_historie("{ kein json")
    main.save_to_history("roh", "ergebnis")
    eintraege = json.loads(datei.read_text(encoding="utf-8"))
    assert eintraege[0]["result"] == "ergebnis"


def test_historie_wird_begrenzt(mit_historie, monkeypatch):
    mit_historie(json.dumps([]))
    monkeypatch.setattr(main, "MAX_HISTORY", 3)
    for i in range(5):
        main.save_to_history(f"roh{i}", f"ergebnis{i}")
    assert len(main.load_history()) == 3
