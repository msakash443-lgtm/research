"""One construct spelled more than one way across the sections (M7.2.2, spec 5.12 quality checks).

Pure: no database, no model, nothing stored. A construct is a run of 2 to 4 words joined by spaces or hyphens in the
author's text. It is flagged when the same words appear with spellings that differ in spacing or hyphenation, for
example "self-efficacy" and "self efficacy". Letter case is ignored, so a capital at the start of a sentence never
creates a variant. A line break or sentence punctuation between two words ends the construct. Synonyms and spelling
differences such as "behaviour" and "behavior" are not detected.
"""

from __future__ import annotations

import re
from typing import Any

MAX_WORDS = 4
_WORD = re.compile(r"[A-Za-z]+")
_JOIN = re.compile(r"[\s-]+")


def find_spelling_variants(sections: dict[str, str]) -> list[dict[str, Any]]:
    """`sections` maps a section name to its text. Returns one entry per construct that has more than one spelling:
    `construct` (the lower-case words, hyphens read as spaces) and `spellings`, each spelling mapped to the sorted
    names of the sections that use it. Spellings are compared lower-cased with runs of whitespace collapsed."""
    seen: dict[str, dict[str, set[str]]] = {}
    for name in sorted(sections):
        text = sections[name]
        words = list(_WORD.finditer(text))
        for start_index, first in enumerate(words):
            for end_index in range(start_index, min(start_index + MAX_WORDS, len(words))):
                if end_index > start_index:
                    gap = text[words[end_index - 1].end() : words[end_index].start()]
                    if not _JOIN.fullmatch(gap):
                        break
                spelling = " ".join(text[first.start() : words[end_index].end()].lower().split())
                key = " ".join(spelling.replace("-", " ").split())
                seen.setdefault(key, {}).setdefault(spelling, set()).add(name)

    variants: list[dict[str, Any]] = []
    for key in sorted(seen):
        spellings = seen[key]
        if len(spellings) > 1:
            variants.append(
                {
                    "construct": key,
                    "spellings": {spelling: sorted(names) for spelling, names in sorted(spellings.items())},
                }
            )
    return variants
