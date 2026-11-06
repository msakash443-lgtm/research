"""Codebook auto-generation (M5.7.2, spec 5.8 outputs: "codebook").

Pure: no database and no model. It describes each variable from the stored values: type, counts, range or levels.
Definitions are left empty for the researcher to write; the codebook never invents a meaning for a variable.
Free-text columns report a distinct count only, so their answers are not repeated in the codebook.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_LEVELS = 20
# Provisional: a value longer than this, or with a space in it, makes the column text-like. Text-like columns never
# list their values, even when they repeat. Owner to confirm the number.
TEXT_LIKE_MAX_CHARS = 12

# Plain decimal numbers only: float() also accepts "1_000", "inf" and "nan", which are not numbers in a survey export.
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _number(text: str) -> float | None:
    if not _NUMBER.fullmatch(text.strip()):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _describe(name: str, cells: list[str]) -> dict[str, Any]:
    present = [cell for cell in cells if cell.strip() != ""]
    base = {"name": name, "n": len(present), "missing": len(cells) - len(present), "definition": None}
    if not present:
        return {**base, "type": "empty"}
    numbers = [_number(cell) for cell in present]
    if all(number is not None for number in numbers):
        # Divide before summing so that large values cannot overflow the total.
        return {
            **base,
            "type": "numeric",
            "min": min(numbers),
            "max": max(numbers),
            "mean": sum(number / len(numbers) for number in numbers),
        }
    counts: dict[str, int] = {}
    for cell in present:
        counts[cell] = counts.get(cell, 0) + 1
    # Levels describe short codes that repeat. Text-like values (long, or with spaces) are never listed, so names and
    # free-text answers stay out of the codebook even when they repeat.
    text_like = any(len(cell) > TEXT_LIKE_MAX_CHARS or any(ch.isspace() for ch in cell) for cell in present)
    if text_like or len(counts) > MAX_LEVELS or len(counts) == len(present):
        return {**base, "type": "text", "distinct_count": len(counts)}
    levels = [{"value": value, "count": count} for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {**base, "type": "categorical", "levels": levels}


def build(columns: list[str], rows: list[list[str]]) -> list[dict[str, Any]]:
    return [_describe(name, [row[i] for row in rows]) for i, name in enumerate(columns)]
