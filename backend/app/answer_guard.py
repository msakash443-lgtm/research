"""Reject a model answer that repeats the prompt's own internals.

If an injected instruction ("print your system prompt") works, the model's answer contains text it
should never have repeated. Such an answer is rejected, not stored: the run fails loudly (plan
rule 22) instead of saving it as if it were a normal result.

Checked: the per-request source-block id (the model never needs to say it), the fence markers
themselves, and any whole sentence of the system prompt quoted back. This catches the common
exfiltration shape; it can't catch a model that paraphrases, and it is not a substitute for the
fence (M1.2.1), cleaning (M1.2.2) or human review.
"""

from __future__ import annotations

import re

_FENCE_MARKER = re.compile(r"<<<\s*(?:END\s+)?SOURCE", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
MIN_SENTENCE_CHARS = 40  # shorter sentences are too generic to count as a leak


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def check_answer(answer: str, *, system_prompt: str, nonce: str) -> str | None:
    """Return what the answer leaked (a short phrase for the error message), or None if it is clean."""
    normalized = _norm(answer)
    if nonce and nonce.lower() in normalized:
        return "an internal source-block id"
    if _FENCE_MARKER.search(answer):
        return "the source-block markers"
    for sentence in _SENTENCE_SPLIT.split(_norm(system_prompt)):
        if len(sentence) >= MIN_SENTENCE_CHARS and sentence in normalized:
            return "text from the system prompt"
    return None
