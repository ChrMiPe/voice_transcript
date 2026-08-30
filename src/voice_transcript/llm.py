import json
import os
import socket
import struct
import subprocess

from voice_transcript.applog import log
from voice_transcript.cleanup import strip_prompt_markers
from voice_transcript.config import LLM_TIMEOUT, UV_PATH, llm_enabled, project_dir
from voice_transcript.llm_server import SOCKET_PATH
from voice_transcript.notify import notify


def _query_server(text):
    """Sendet Text an den persistenten LLM-Server."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(LLM_TIMEOUT)
    sock.connect(SOCKET_PATH)

    try:
        payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        sock.sendall(struct.pack(">I", len(payload)) + payload)

        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                raise ConnectionError("Server hat die Verbindung geschlossen")
            header += chunk

        msg_len = struct.unpack(">I", header)[0]

        data = b""
        while len(data) < msg_len:
            chunk = sock.recv(min(4096, msg_len - len(data)))
            if not chunk:
                raise ConnectionError("Server hat die Verbindung geschlossen")
            data += chunk

        response = json.loads(data.decode("utf-8"))
        if response.get("ok") and response.get("result"):
            # Der Server sagt mit, wenn er die Bereinigung verworfen hat — sonst
            # sieht ein zurueckgegebener Rohtext wie ein bereinigter aus.
            notice = response.get("notice")
            if notice:
                notify("LLM übersprungen", notice)
            return response["result"]
        return None
    finally:
        sock.close()


def _query_subprocess(text):
    """Fallback: LLM als Subprocess ausfuehren."""
    env = os.environ.copy()
    env["LANG"] = "de_DE.UTF-8"
    env["LC_ALL"] = "de_DE.UTF-8"
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [UV_PATH, "run", "--project", project_dir(),
         "python", "-m", "voice_transcript.llm_worker"],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=LLM_TIMEOUT,
        env=env,
    )

    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _saeubern(result, text):
    """Markierungen aus der Modellausgabe entfernen, sonst den Rohtext.

    Auch hier und nicht nur im Server: der Server laeuft als eigener Prozess und
    ueberlebt ein Update der App. Bis er neu startet, liefert er weiter Ausgaben
    mit Markierungen — diese Zeile ist die letzte Station vor dem Editor.
    """
    # Verwirft der Server die Bereinigung, gibt er den *Rohtext* zurueck — dann
    # ist nichts zu saeubern, und die Bereinigung wuerde sich am Diktat selbst
    # vergreifen: aus „Transkript: Meeting mit Anna." wuerde „Meeting mit Anna.".
    # Genau der Pfad, der unangetasteten Text zusichert, haette ihn angetastet.
    if result == text:
        return text

    sauber = strip_prompt_markers(result)
    if sauber:
        return sauber
    log("LLM-Ausgabe bestand nur aus Markierungen — nehme den unbereinigten Text")
    return text


def llm_polish(text):
    if not text or not llm_enabled():
        return text

    try:
        # Zuerst den persistenten Server versuchen
        if os.path.exists(SOCKET_PATH):
            result = _query_server(text)
            if result:
                return _saeubern(result, text)

        # Fallback auf Subprocess
        result = _query_subprocess(text)
        if result:
            return _saeubern(result, text)

        return text

    except Exception as e:
        log(f"LLM nicht nutzbar: {type(e).__name__}: {e}")
        notify("LLM übersprungen", f"Fallback auf Regex: {str(e)[:60]}")
        return text
