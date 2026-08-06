"""Spracherkennung: eigene Aufnahme + Whisper, mit `yap` als Rueckfall.

Zwei Engines mit derselben Schnittstelle, weil sie unterschiedlich funktionieren:

- **whisper** — wir nehmen selbst auf (recorder.py) und lassen den LLM-Server
  transkribieren. Nur dort liegt MLX; das App-Bundle enthaelt es absichtlich nicht.
  Der Gewinn ist das Fachvokabular: der Vokabular-Hinweis spannt die Dekodierung
  vor, gemessen 7/10 auf 10/10 Fachbegriffe.
- **yap** — Apple Speech nimmt selbst auf und wird per SIGINT gestoppt. Schneller
  und ohne eigenen Speicher, kann aber kein Vokabular vorgespannt bekommen.

Der Rueckfall ist zweistufig, damit ein Whisper-Problem kein Diktat kostet:

1. Transkription scheitert (Server aus, Modell fehlt) -> `yap transcribe` bekommt
   **dieselbe Aufnahme**. Nichts muss wiederholt werden.
2. Schon die Aufnahme scheitert (kein Mikrofon, AVFoundation fehlt) -> `yap dictate`
   nimmt selbst auf, wie vor der Umstellung.
"""
import json
import os
import signal
import socket
import struct
import subprocess
import threading

from voice_transcript.applog import log
from voice_transcript.config import (
    ASR_ENGINE,
    LLM_TIMEOUT,
    PID_FILE,
    YAP_LOCALE,
    YAP_PATH,
    load_settings,
)
from voice_transcript.llm_server import SOCKET_PATH

# Zeitgrenze fuer `yap transcribe` auf eine fertige Datei. yap laeuft mit ~59x
# Echtzeit, das ist also selbst fuer lange Diktate reichlich.
YAP_TRANSCRIBE_TIMEOUT = 120

_active = None
_active_lock = threading.Lock()


def engine():
    """Gewaehlte Engine. Die Einstellung schlaegt die Voreinstellung im Code."""
    gewaehlt = load_settings().get("asr_engine", ASR_ENGINE)
    return gewaehlt if gewaehlt in ("whisper", "yap") else ASR_ENGINE


# ─── yap ───

def _yap_transcribe_file(path):
    """Transkribiert eine fertige Aufnahme mit yap. Rueckgabe: (text, fehler)."""
    try:
        res = subprocess.run(
            [YAP_PATH, "transcribe", path, "--locale", YAP_LOCALE, "--txt"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=YAP_TRANSCRIBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"yap transcribe fehlgeschlagen: {type(e).__name__}: {e}"

    if res.returncode != 0:
        return None, f"yap transcribe exit {res.returncode}: {(res.stderr or '').strip()[:200]}"
    return (res.stdout or "").strip(), None


class _YapSession:
    """`yap dictate` nimmt selbst auf; gestoppt wird per SIGINT."""

    def __init__(self, process):
        self.process = process

    def stop(self):
        try:
            os.kill(self.process.pid, signal.SIGINT)
        except OSError as e:
            log(f"SIGINT an yap (PID {self.process.pid}) fehlgeschlagen: {e}")

    def result(self):
        stdout, stderr = self.process.communicate()
        text = (stdout or "").strip()
        if text:
            return text, None
        detail = (stderr or "").strip()
        return None, detail or "Kein Text erkannt"


def _start_yap():
    try:
        process = subprocess.Popen(
            [YAP_PATH, "dictate"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
    except OSError as e:
        return None, f"yap nicht startbar: {e}"

    # PID-Datei wie bisher: nur so kann ein *anderer* Prozess die Aufnahme stoppen
    # (CLI, Raycast).
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(process.pid))
    except OSError as e:
        log(f"PID-Datei nicht schreibbar: {e}")

    return _YapSession(process), None


# ─── Whisper ───

def _server_transcribe(path):
    """Laesst den LLM-Server transkribieren. Rueckgabe: (text, fehler)."""
    if not os.path.exists(SOCKET_PATH):
        return None, "LLM-Server nicht erreichbar (kein Socket)"

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(LLM_TIMEOUT)
    try:
        sock.connect(SOCKET_PATH)
        payload = json.dumps({"action": "transcribe", "path": path}).encode("utf-8")
        sock.sendall(struct.pack(">I", len(payload)) + payload)

        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return None, "Server hat die Verbindung geschlossen"
            header += chunk

        laenge = struct.unpack(">I", header)[0]
        daten = b""
        while len(daten) < laenge:
            chunk = sock.recv(min(4096, laenge - len(daten)))
            if not chunk:
                return None, "Server hat die Verbindung geschlossen"
            daten += chunk

        antwort = json.loads(daten.decode("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        sock.close()

    if not antwort.get("ok"):
        return None, antwort.get("error") or "Server meldet Fehler"
    return (antwort.get("result") or "").strip(), None


class _WhisperSession:
    """Eigene Aufnahme; transkribiert wird erst nach dem Stoppen."""

    def __init__(self, recorder):
        self.recorder = recorder
        self._stopped = threading.Event()

    def stop(self):
        self.recorder.stop()
        self._stopped.set()

    def result(self):
        # Blockiert, bis der Nutzer stoppt — dasselbe Verhalten wie yaps
        # communicate(), damit dictate() nicht zwei Ablaeufe kennen muss.
        self._stopped.wait()

        try:
            if not self.recorder.has_audio():
                return None, "Keine Aufnahme entstanden"

            text, fehler = _server_transcribe(self.recorder.path)
            if fehler is None and text:
                return text, None

            # Ein leeres Ergebnis zaehlt wie ein Fehler und loest denselben
            # Rueckfall aus. Genau das war noetig: ein Server, der die
            # Transkriptions-Anfrage noch nicht kennt, antwortet mit ok und leerem
            # Text — ohne diese Zeile sah das nach „nichts gesagt" aus und der
            # Rueckfall lief nie an.
            fehler = fehler or "Server lieferte keinen Text (zu alt?)"

            # Stufe 1: dieselbe Aufnahme, andere Engine. Nichts wiederholen.
            log(f"Whisper nicht nutzbar ({fehler}) — weiche auf yap transcribe aus")
            text, yap_fehler = _yap_transcribe_file(self.recorder.path)
            if yap_fehler:
                return None, f"{fehler} / {yap_fehler}"
            return (text, None) if text else (None, "Kein Text erkannt")
        finally:
            self.recorder.cleanup()


def _start_whisper():
    from voice_transcript.recorder import Recorder

    recorder = Recorder()
    fehler = recorder.start()
    if fehler:
        recorder.cleanup()
        return None, fehler
    return _WhisperSession(recorder), None


# ─── Schnittstelle ───

def start():
    """Startet eine Aufnahme. Rueckgabe: (session, engine_name) oder (None, fehler).

    Bei `whisper` ist der zweite Wert die tatsaechlich benutzte Engine — scheitert
    die eigene Aufnahme, kommt hier "yap" zurueck.
    """
    global _active

    # Die Sperre umschliesst den *ganzen* Start, nicht nur die Zuweisung. Sonst
    # liegt zwischen "Aufnahme laeuft" und "Sitzung registriert" ein Fenster, in
    # dem stop_active() die Sitzung nicht findet und den Hotkey-Druck verschluckt —
    # die Aufnahme lief dann weiter, ohne dass sie noch stoppbar war.
    with _active_lock:
        gewaehlt = engine()
        benutzt = gewaehlt

        if gewaehlt == "whisper":
            session, fehler = _start_whisper()
            if session is None:
                # Stufe 2: nicht mal aufnehmen moeglich — yap nimmt selbst auf.
                log(f"Aufnahme nicht startbar ({fehler}) — weiche auf yap dictate aus")
                session, yap_fehler = _start_yap()
                benutzt = "yap"
                if session is None:
                    return None, f"{fehler} / {yap_fehler}"
        else:
            session, fehler = _start_yap()
            if session is None:
                return None, fehler

        _active = session
    return session, benutzt


def stop_active():
    """Stoppt die laufende Aufnahme. True, wenn eine lief."""
    global _active
    with _active_lock:
        session = _active
        _active = None
    if session is None:
        return False
    session.stop()
    return True


def finish(session):
    """Ergebnis abholen und die Sitzung abmelden. Rueckgabe: (text, fehler)."""
    global _active
    try:
        return session.result()
    finally:
        with _active_lock:
            if _active is session:
                _active = None
