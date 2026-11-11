"""Clean text from outside (web pages, PDFs, abstracts, titles) before it goes into a prompt.

This produces the *model-facing copy* only. The stored excerpt is immutable, hashed evidence and is
never changed; the cleaned copy is what the run snapshot records, so a reviewer sees exactly what
the model was given, plus the `flags` describing what was found.

What it does, in order:
  1. removes content a person cannot see but a model reads: HTML comments, script/style/iframe
     blocks, and invisible or control characters (zero-width, bidi overrides, Unicode tag
     characters, private use);
  2. neutralises markup that imitates the prompt's own structure: fence lookalikes (`<<<`, `>>>`)
     and chat-template tokens (`<|im_start|>`, `[INST]`, `<<SYS>>`);
  3. collapses whitespace and caps the length (the cap is applied after cleaning, ends in `…`);
  4. *flags* instruction-like phrases ("ignore previous instructions", "you are now", ...). These are
     reported, never deleted: removing words from evidence would silently change what a source says.

Pattern matching can't stop a determined injection. It lowers the chance and makes the attempt
visible; the fence in the prompt (M1.2.1) and human review remain the real defences.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --- removed ---------------------------------------------------------------------------------
_HIDDEN_HTML = re.compile(
    r"<!--.*?(?:-->|$)|<(script|style|iframe|object|embed|template)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE
)
_KEEP_WHITESPACE = {"\n", "\r", "\t"}


def _strip_invisible(text: str) -> tuple[str, int]:
    """Drop control (Cc), format (Cf: zero-width, bidi, tag characters), private-use (Co) and surrogate (Cs) characters."""
    kept, removed = [], 0
    for ch in text:
        if ch not in _KEEP_WHITESPACE and unicodedata.category(ch) in {"Cc", "Cf", "Co", "Cs"}:
            removed += 1
        else:
            kept.append(ch)
    return "".join(kept), removed


# --- neutralised -----------------------------------------------------------------------------
_CHAT_TOKENS = re.compile(r"<\|[^|<>\n]{1,40}\|>|\[/?INST\]|<<\s*/?SYS\s*>>", re.IGNORECASE)
_FENCE_LOOKALIKE = re.compile(r"<{3,}|>{3,}")

# --- flagged only ----------------------------------------------------------------------------
_FLAG_PATTERNS = {
    "ignore_instructions": re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,50}?\b(?:previous|prior|above|earlier|preceding|all|any|your)\b"
        r".{0,50}?\b(?:instructions?|prompts?|rules?|guidelines?|directions?)\b",
        re.IGNORECASE,
    ),
    "role_override": re.compile(
        r"\b(?:you are now|from now on,? you|act as|pretend (?:to be|you are)|new instructions?|system override)\b",
        re.IGNORECASE,
    ),
    "prompt_exfiltration": re.compile(
        r"\b(?:reveal|show|print|repeat|output|leak|display)\b.{0,40}?"
        r"\b(?:system prompt|hidden prompt|your (?:prompt|instructions)|initial instructions)\b",
        re.IGNORECASE,
    ),
    "role_marker": re.compile(r"(?:^|\s)(?:system|assistant|developer)\s*:\s", re.IGNORECASE),
}


# Non-English phrasings of the same attacks (French, Spanish, German). Matched on the folded copy.
_FLAG_PATTERNS["ignore_instructions_multilingual"] = re.compile(
    r"\b(?:ignorez|oubliez|ignora|olvida|ignoriere|vergiss|missachte)\b.{0,50}?"
    r"\b(?:instructions?|consignes?|instrucciones|anweisungen|regeln|r.gles|pr.c.dent\w*|anterior\w*|vorherig\w*)",
    re.IGNORECASE,
)

# Cyrillic/Greek letters that render like Latin ones; used only to build the matching copy.
_CONFUSABLES = str.maketrans(
    "аеорсухіјѕԁԛ" "АВЕКМНОРСТХ" "οαεινρτυ" "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",
    "aeopcyxijsdq" "ABEKMHOPCTX" "oaeinptu" "ABEZHIKMNOPTYX",
)


def _fold_for_matching(text: str) -> str:
    """NFKC + map look-alike letters to Latin so evasion by homoglyph doesn't hide a phrase. Never stored."""
    return unicodedata.normalize("NFKC", text).translate(_CONFUSABLES)


@dataclass(frozen=True)
class Cleaned:
    text: str
    flags: tuple[str, ...]  # sorted, unique: what was found/changed
    truncated: bool


def clean_untrusted(text: str, max_chars: int) -> Cleaned:
    """Return the model-facing version of `text`, capped at `max_chars` characters."""
    flags: set[str] = set()

    without_hidden = _HIDDEN_HTML.sub(" ", text)
    if without_hidden != text:
        flags.add("hidden_html")

    visible, removed = _strip_invisible(without_hidden)
    if removed:
        flags.add("hidden_characters")

    neutral = _CHAT_TOKENS.sub("[chat-markup removed]", visible)
    if neutral != visible:
        flags.add("chat_markup")
    fenced = _FENCE_LOOKALIKE.sub(lambda m: " ".join(m.group(0)), neutral)
    if fenced != neutral:
        flags.add("fence_lookalike")

    compact = " ".join(fenced.split())
    folded = _fold_for_matching(compact)
    for name, pattern in _FLAG_PATTERNS.items():
        if pattern.search(compact) or pattern.search(folded):
            flags.add(name)

    truncated = len(compact) > max_chars
    final = f"{compact[: max_chars - 1]}…" if truncated else compact
    return Cleaned(final, tuple(sorted(flags)), truncated)
