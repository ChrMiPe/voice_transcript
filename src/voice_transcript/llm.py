import json
import os
import socket
import struct
import subprocess

from voice_transcript.config import LLM_ENABLED, UV_PATH, project_dir
from voice_transcript.llm_server import SOCKET_PATH
from voice_transcript.notify import notify


def _query_server(text):
    """Sendet Text an den persistenten LLM-Server."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(30)
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
        timeout=30,
        env=env,
    )

    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def llm_polish(text):
    if not text or not LLM_ENABLED:
        return text

    try:
        # Zuerst den persistenten Server versuchen
        if os.path.exists(SOCKET_PATH):
            result = _query_server(text)
            if result:
                return result

        # Fallback auf Subprocess
        result = _query_subprocess(text)
        if result:
            return result

        return text

    except Exception as e:
        notify("LLM Skip", f"Fallback auf Regex: {str(e)[:50]}")
        return text
