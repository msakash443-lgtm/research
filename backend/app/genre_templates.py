"""Section outlines by manuscript genre, and checks of a draft against them (M6.10.1, spec 5.11.2).

Pure: no database, no model, nothing stored. The outlines are starting points named after the genres in the spec
(IMRaD, conceptual paper, review paper, thesis chapter); the scholar edits them, and a draft that departs from an
outline is reported, never rewritten. Keyword checks take the limit as an argument, because each journal sets its own.
"""

from __future__ import annotations

GENRES: dict[str, tuple[str, ...]] = {
    "imrad": ("Introduction", "Methods", "Results", "Discussion"),
    "conceptual": ("Introduction", "Conceptual background", "Propositions", "Discussion"),
    "review": ("Introduction", "Method", "Findings", "Discussion"),
    "thesis_chapter": ("Introduction", "Literature review", "Method", "Results", "Discussion", "Conclusion"),
}


class GenreError(ValueError):
    """An unknown genre, or a check that cannot be run; the message is shown to the researcher."""


def outline(genre: str) -> list[str]:
    if genre not in GENRES:
        raise GenreError(f"Unknown genre {genre!r}; known: {', '.join(sorted(GENRES))}.")
    return list(GENRES[genre])


def check_sections(genre: str, sections: list[str]) -> dict[str, object]:
    """Compare a draft's section headings with its genre's outline.

    `missing`: outline sections the draft lacks. `unexpected`: draft sections the outline does not name, which may be
    fine. `out_of_order`: the outline sections the draft does have appear in a different order than the outline.
    """
    template = outline(genre)
    present = [s for s in sections if s in template]
    expected_order = [s for s in template if s in sections]
    return {
        "missing": [s for s in template if s not in sections],
        "unexpected": [s for s in sections if s not in template],
        "out_of_order": present != expected_order,
    }


def check_keywords(keywords: list[str], max_count: int) -> list[str]:
    """Problems with a keyword list: blank entries, case-insensitive duplicates, and more than `max_count`."""
    problems: list[str] = []
    seen: set[str] = set()
    for index, keyword in enumerate(keywords, start=1):
        text = keyword.strip()
        if not text:
            problems.append(f"keyword {index} is blank")
            continue
        if text.casefold() in seen:
            problems.append(f"duplicate keyword: {text!r}")
        seen.add(text.casefold())
    if len(keywords) > max_count:
        problems.append(f"{len(keywords)} keywords; the limit is {max_count}")
    return problems
