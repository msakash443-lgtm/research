"""Word limits from the target journal's author guidelines (M7.6.1, spec 5.13.3).

Pure: no database, no model, nothing stored. The limits are the author's, copied from the venue's guidelines; this
module invents none. Word count is whitespace-separated tokens, which is how most submission systems count.
"""

from __future__ import annotations

TOTAL = "total"


def word_count(text: str) -> int:
    return len(text.split())


def check_word_limits(sections: dict[str, str], limits: dict[str, int]) -> list[dict[str, object]]:
    """Sections (or the whole draft, under the key "total") that are over their limit.

    Raises ValueError for a limit on a section the draft does not have, so a misspelt heading cannot silently pass,
    and for a limit that is not a positive whole number.
    """
    for name, limit in limits.items():
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError(f"The limit for {name!r} must be a positive whole number.")
        if name != TOTAL and name not in sections:
            raise ValueError(f"There is a limit for {name!r}, but the draft has no such section.")
    problems: list[dict[str, object]] = []
    for name, limit in limits.items():
        words = sum(word_count(text) for text in sections.values()) if name == TOTAL else word_count(sections[name])
        if words > limit:
            problems.append({"section": name, "words": words, "limit": limit})
    return problems
