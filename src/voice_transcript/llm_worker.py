"""Standalone LLM Worker — wird als Subprocess aufgerufen.
Liest Text von stdin, gibt polierten Text auf stdout aus.

Bewusst dieselben Schutzregeln wie llm_server.py: der Worker ist der Rueckfall,
wenn der Server nicht laeuft — und ein Rueckfall, der stumm Text abschneidet, ist
schlimmer als keiner.
"""
import sys
import re

from voice_transcript.config import (
    MIN_LENGTH_RATIO,
    MLX_MAX_TOKENS,
    MLX_MODEL,
    MLX_TEMPERATURE,
    SYSTEM_PROMPT,
    TOKEN_BUDGET_FACTOR,
    TOKEN_BUDGET_MARGIN,
    USER_TEMPLATE,
)
from voice_transcript.glossary import prompt_section


def strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def main():
    text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    from mlx_lm import load, stream_generate
    import mlx_lm.sample_utils as su

    model, tokenizer = load(MLX_MODEL)
    sampler = su.make_sampler(temp=MLX_TEMPERATURE)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + prompt_section()},
        {"role": "user", "content": USER_TEMPLATE.format(text=text)},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
    )

    # Budget an der Eingabe bemessen, wie im Server — ein fester Deckel hat lange
    # Diktate mitten im Satz abgeschnitten.
    max_tokens = min(
        int(len(tokenizer.encode(text)) * TOKEN_BUDGET_FACTOR) + TOKEN_BUDGET_MARGIN,
        MLX_MAX_TOKENS,
    )

    # stream_generate statt generate(): nur der Stream liefert finish_reason, und
    # ein abgeschnittener Satz sieht wie ein fertiger aus.
    chunks = []
    last = None
    for response in stream_generate(
        model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=sampler
    ):
        chunks.append(response.text)
        last = response

    truncated = last is not None and last.finish_reason == "length"
    result = strip_thinking("".join(chunks)).strip()

    # Ein halber Satz ist schlimmer als ein unbereinigter ganzer.
    if truncated or not result or len(result) < len(text) * MIN_LENGTH_RATIO:
        print(text, end="")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
