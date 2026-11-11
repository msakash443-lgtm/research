"""Qualitative transcript anonymisation (M5.7.3, spec 5.8.5).

Pure: no database, no model, no network. The researcher lists the terms to remove (names, places, employers). Each
whole-word, case-insensitive match becomes a numbered placeholder, the same placeholder for the same term. The log
records only counts per term number, never the terms, so the log cannot re-identify anyone.

This does not find identifiers by itself. It removes only what the researcher lists, and says so in the output.
"""

from __future__ import annotations

import re
from typing import Any

MAX_TERMS = 200
MAX_TERM_CHARS = 200
_PLACEHOLDER = re.compile(r"\[REDACTED-\d+\]")


def redact(text: str, terms: list[str]) -> tuple[str, list[dict[str, int]]]:
    cleaned = [term.strip() for term in terms]
    if not cleaned or any(term == "" for term in cleaned):
        raise ValueError("List at least one term to remove; blank terms are not allowed.")
    if len(cleaned) > MAX_TERMS or any(len(term) > MAX_TERM_CHARS for term in cleaned):
        raise ValueError(f"At most {MAX_TERMS} terms of up to {MAX_TERM_CHARS} characters each.")

    # Text that already holds a placeholder cannot be told apart from one this function writes, so refuse it.
    if _PLACEHOLDER.search(text):
        raise ValueError("The text already contains [REDACTED-n] placeholders; redact the original text instead.")

    # One placeholder number per distinct term, ignoring case; numbered in the order the researcher listed them.
    # Each term gets its own named group, so the match is mapped back by group, not by lower-casing the matched text
    # (lower-casing is not stable for every Unicode letter, e.g. "ſ" and "İ").
    number_of: dict[str, int] = {}
    term_of: dict[int, str] = {}
    for term in cleaned:
        key = term.lower()
        if key not in number_of:
            number_of[key] = len(number_of) + 1
            term_of[number_of[key]] = term
    counts = dict.fromkeys(term_of, 0)

    # Longest first, so "Anna" wins over "Ann" when both are listed.
    alternatives = [
        f"(?P<t{number}>{re.escape(term_of[number])})"
        for number in sorted(term_of, key=lambda n: len(term_of[n]), reverse=True)
    ]
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)

    def _replace(match: re.Match[str]) -> str:
        number = int(match.lastgroup[1:])
        counts[number] += 1
        return f"[REDACTED-{number}]"

    out = pattern.sub(_replace, text)
    log: list[dict[str, Any]] = [{"term_index": number, "count": counts[number]} for number in sorted(counts)]
    return out, log
