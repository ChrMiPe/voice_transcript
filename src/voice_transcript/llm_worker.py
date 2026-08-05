"""Standalone LLM Worker — wird als Subprocess aufgerufen.
Liest Text von stdin, gibt polierten Text auf stdout aus.
"""
import sys
import re

from voice_transcript.config import (
    MLX_MAX_TOKENS,
    MLX_MODEL,
    MLX_TEMPERATURE,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
)


def strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def main():
    text = sys.stdin.read().strip()
    if not text:
        sys.exit(0)

    from mlx_lm import load, generate
    import mlx_lm.sample_utils as su

    model, tokenizer = load(MLX_MODEL)
    sampler = su.make_sampler(temp=MLX_TEMPERATURE)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(text=text)},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
    )

    result = generate(
        model, tokenizer, prompt=prompt, max_tokens=MLX_MAX_TOKENS, sampler=sampler
    )
    result = strip_thinking(result).strip()

    if result and len(result) > len(text) * 0.3:
        print(result, end="")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
