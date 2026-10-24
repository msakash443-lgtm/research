"""Word limits from the target journal's author guidelines (M7.6.1, spec 5.13.3).

Pure: no database, no model, nothing stored. The limits are the author's, copied from the venue's guidelines; this
module invents none. Word count is whitespace-separated tokens, which is how most submission systems count.
"""

from __future__ import annotations

from app.genre_templates import check_sections

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


def missing_sections(genre: str, sections: list[str]) -> list[str]:
    """Outline sections of `genre` (M6.10.1) that the draft has no heading for. Unknown genres raise GenreError."""
    return check_sections(genre, sections)["missing"]


FIGURE_LIMIT_KEYS = frozenset({"max_width_mm", "min_dpi"})


def check_figure_specs(figures: list[dict[str, object]], limits: dict[str, float]) -> list[dict[str, object]]:
    """Figures wider than `max_width_mm` or below `min_dpi`, as the author's guidelines state them.

    Each figure is {"name": str, "width_mm": number, "dpi": number}. A limit the author leaves out is not checked.
    Raises ValueError for an unknown limit key, a limit that is not a positive number, or a figure whose width or dpi
    is not a positive number, so a bad spec cannot silently pass.
    """
    for key, limit in limits.items():
        if key not in FIGURE_LIMIT_KEYS:
            raise ValueError(f"Unknown figure limit {key!r}.")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
            raise ValueError(f"The limit for {key!r} must be a positive number.")
    problems: list[dict[str, object]] = []
    for figure in figures:
        name = str(figure["name"])
        for measure in ("width_mm", "dpi"):
            value = figure[measure]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"Figure {name!r} needs a positive {measure}.")
        max_width = limits.get("max_width_mm")
        if max_width is not None and figure["width_mm"] > max_width:
            problems.append({"figure": name, "measure": "width_mm", "value": figure["width_mm"], "limit": max_width})
        min_dpi = limits.get("min_dpi")
        if min_dpi is not None and figure["dpi"] < min_dpi:
            problems.append({"figure": name, "measure": "dpi", "value": figure["dpi"], "limit": min_dpi})
    return problems
