"""Persistenter LLM-Server — laed das Modell einmal und wartet auf Anfragen via Unix-Socket."""

import json
import os
import re
import socket
import struct
import sys
import threading

from voice_transcript.config import (
    MLX_MAX_TOKENS,
    MLX_MODEL,
    MLX_TEMPERATURE,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
)

SOCKET_PATH = "/tmp/voice_transcript_llm.sock"
PID_FILE = "/tmp/voice_transcript_llm.pid"


def strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class LLMServer:
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.sampler = None
        self._lock = threading.Lock()

    def load_model(self):
        from mlx_lm import load
        import mlx_lm.sample_utils as su

        self.model, self.tokenizer = load(MLX_MODEL)
        self.sampler = su.make_sampler(temp=MLX_TEMPERATURE)

    def generate(self, text):
        from mlx_lm import generate

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(text=text)},
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
        )

        with self._lock:
            result = generate(
                self.model,
                self.tokenizer,
                prompt=prompt,
                max_tokens=MLX_MAX_TOKENS,
                sampler=self.sampler,
            )

        result = strip_thinking(result).strip()

        if result and len(result) > len(text) * 0.3:
            return result
        return text

    def handle_client(self, conn):
        try:
            # Protokoll: 4 Bytes Laenge (big-endian), dann JSON-Payload
            header = b""
            while len(header) < 4:
                chunk = conn.recv(4 - len(header))
                if not chunk:
                    return
                header += chunk

            msg_len = struct.unpack(">I", header)[0]

            data = b""
            while len(data) < msg_len:
                chunk = conn.recv(min(4096, msg_len - len(data)))
                if not chunk:
                    return
                data += chunk

            request = json.loads(data.decode("utf-8"))
            text = request.get("text", "")

            if not text:
                response = {"result": "", "ok": True}
            else:
                result = self.generate(text)
                response = {"result": result, "ok": True}

        except Exception as e:
            response = {"result": "", "ok": False, "error": str(e)}

        try:
            resp_data = json.dumps(response, ensure_ascii=False).encode("utf-8")
            conn.sendall(struct.pack(">I", len(resp_data)) + resp_data)
        except Exception:
            pass
        finally:
            conn.close()

    def run(self):
        # Alten Socket aufraemen
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        self.load_model()

        # PID schreiben
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        server.listen(4)

        try:
            while True:
                conn, _ = server.accept()
                thread = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                thread.start()
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
            if os.path.exists(SOCKET_PATH):
                os.remove(SOCKET_PATH)
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)


def is_running():
    """Prueft ob der LLM-Server laeuft."""
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        # Prozess existiert nicht mehr — aufraemen
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        return False


def stop_server():
    """Stoppt den LLM-Server."""
    import signal

    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    # Aufraemen
    for path in (PID_FILE, SOCKET_PATH):
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    LLMServer().run()
