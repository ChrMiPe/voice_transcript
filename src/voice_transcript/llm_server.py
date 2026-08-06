"""Persistenter LLM-Server — laed das Modell einmal und wartet auf Anfragen via Unix-Socket."""

import json
import os
import re
import socket
import struct
import sys
import threading
import wave

from voice_transcript.applog import log
from voice_transcript.glossary import prompt_section, whisper_prompt
from voice_transcript.config import (
    MIN_LENGTH_RATIO,
    MLX_MAX_TOKENS,
    MLX_MODEL,
    MLX_TEMPERATURE,
    SYSTEM_PROMPT,
    TOKEN_BUDGET_FACTOR,
    TOKEN_BUDGET_MARGIN,
    USER_TEMPLATE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
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

    def transcribe(self, wav_path):
        """Transkribiert eine WAV-Datei mit Whisper. Rueckgabe: (text, fehler).

        Whisper laeuft hier und nicht in der App, weil das App-Bundle MLX
        absichtlich nicht enthaelt (siehe build_app.spec/excludes) — derselbe Grund,
        aus dem dieser Server ueberhaupt existiert. Nebeneffekt: das Modell bleibt
        zwischen Diktaten warm, ein Kaltstart kostet nur beim ersten Mal.
        """
        if not os.path.exists(wav_path):
            return None, f"Aufnahme nicht gefunden: {wav_path}"

        try:
            import mlx_whisper
            import numpy as np
        except ImportError as e:
            return None, f"Whisper nicht verfuegbar: {e}"

        try:
            with wave.open(wav_path) as handle:
                frames = handle.getnframes()
                if not frames:
                    return None, "Aufnahme ist leer"
                rohdaten = handle.readframes(frames)
        except (OSError, wave.Error) as e:
            return None, f"Aufnahme nicht lesbar: {type(e).__name__}: {e}"

        samples = np.frombuffer(rohdaten, dtype=np.int16).astype(np.float32) / 32768.0

        # Der Vokabular-Hinweis ist der eigentliche Gewinn gegenueber Apple Speech:
        # gemessen 8/10 auf 10/10 Fachbegriffe, und er kostet keine Zeit (1,27s zu
        # 1,29s auf 20s Audio).
        hinweis = whisper_prompt() or None

        # Serialisiert wie die Textbereinigung: beide Modelle teilen die GPU.
        with self._lock:
            try:
                ergebnis = mlx_whisper.transcribe(
                    samples,
                    path_or_hf_repo=WHISPER_MODEL,
                    language=WHISPER_LANGUAGE,
                    fp16=True,
                    initial_prompt=hinweis,
                )
            except Exception as e:
                log(f"Whisper fehlgeschlagen: {type(e).__name__}: {e}")
                return None, f"{type(e).__name__}: {e}"

        return (ergebnis.get("text") or "").strip(), None

    def _token_budget(self, text):
        """Obergrenze fuer die Ausgabe, an der Eingabe bemessen.

        Bereinigen heisst nicht erfinden — die Ausgabe ist ungefaehr so lang wie
        die Eingabe, mit etwas Luft fuer Interpunktion und Absaetze. Ein fester
        Deckel von 1024 Tokens hat lange Diktate stumm abgeschnitten: bei 4,10
        Zeichen pro Token (gemessen mit diesem Tokenizer) waren das ~4.200
        Zeichen, also rund fuenf Minuten Sprechen.
        """
        prompt_tokens = len(self.tokenizer.encode(text))
        budget = int(prompt_tokens * TOKEN_BUDGET_FACTOR) + TOKEN_BUDGET_MARGIN
        return min(budget, MLX_MAX_TOKENS)

    def generate(self, text):
        """Bereinigt den Text. Rueckgabe: (ergebnis, hinweis).

        `hinweis` ist None im Normalfall, sonst der Grund, warum die
        LLM-Bereinigung verworfen wurde — der Aufrufer meldet das dem Nutzer.
        """
        from mlx_lm import stream_generate

        # Glossar bei jeder Anfrage frisch: so wirkt eine Aenderung an
        # glossary.json sofort, ohne den Server neu zu starten.
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + prompt_section()},
            {"role": "user", "content": USER_TEMPLATE.format(text=text)},
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
        )
        max_tokens = self._token_budget(text)

        # stream_generate statt generate(): nur der Stream liefert finish_reason.
        # Ohne den war nicht zu erkennen, ob das Modell fertig war oder ans Limit
        # gestossen ist — und ein abgeschnittener Satz sieht wie ein fertiger aus.
        with self._lock:
            chunks = []
            last = None
            for response in stream_generate(
                self.model,
                self.tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=self.sampler,
            ):
                chunks.append(response.text)
                last = response
            raw = "".join(chunks)

        truncated = last is not None and last.finish_reason == "length"
        result = strip_thinking(raw).strip()

        if truncated:
            # Ein halber Satz ist schlimmer als ein unbereinigter ganzer.
            log(
                f"LLM-Ausgabe am Limit abgeschnitten ({last.generation_tokens}/"
                f"{max_tokens} Tokens) — nehme den unbereinigten Text"
            )
            return text, "Text zu lang für die LLM-Bereinigung"

        if not result:
            log("LLM-Ausgabe leer — nehme den unbereinigten Text")
            return text, "LLM lieferte kein Ergebnis"

        # Deutlich kuerzer als die Eingabe heisst: das Modell hat zusammengefasst
        # oder halluziniert, statt zu bereinigen.
        if len(result) < len(text) * MIN_LENGTH_RATIO:
            log(
                f"LLM-Ausgabe zu kurz ({len(result)} statt mind. "
                f"{int(len(text) * MIN_LENGTH_RATIO)} Zeichen) — nehme den unbereinigten Text"
            )
            return text, "LLM-Ergebnis verworfen (zu kurz)"

        return result, None

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
            # Ohne "action" ist es eine Bereinigungs-Anfrage — so war das Protokoll
            # vorher, und aeltere Aufrufer sollen weiter funktionieren.
            action = request.get("action", "polish")

            if action == "transcribe":
                text, fehler = self.transcribe(request.get("path", ""))
                if fehler:
                    response = {"result": "", "ok": False, "error": fehler}
                else:
                    response = {"result": text, "ok": True}
            else:
                text = request.get("text", "")
                if not text:
                    response = {"result": "", "ok": True}
                else:
                    result, notice = self.generate(text)
                    response = {"result": result, "ok": True, "notice": notice}

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
