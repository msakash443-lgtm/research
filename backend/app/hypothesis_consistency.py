"""Structural hypothesis alignment across manuscript sections (M7.2.3, spec 5.12).

Pure: no database, model, or network. Hypothesis labels are structural identifiers only;
this checker does not infer whether a result supports or contradicts a hypothesis.
"""

from __future__ import annotations

import re
from typing import Any

_LABEL = re.compile(r"\bH\s*(\d+)\b", re.IGNORECASE)


def _labels(text: str) -> set[str]:
    return {f"H{int(number)}" for number in _LABEL.findall(text)}


def find_hypothesis_alignment(sections: dict[str, str]) -> dict[str, Any]:
    """Compare hypothesis labels across ``hypotheses``, ``results`` and ``conclusions``.

    The ``hypotheses`` section defines the expected labels. The result contains sorted
    labels found in each section, labels missing from each required downstream section,
    and labels used downstream without a declaration. Section names are required and
    missing text is treated as empty. Labels are case-insensitive and whitespace between
    ``H`` and the number is accepted (``h 1`` becomes ``H1``).
    """
    expected = _labels(sections.get("hypotheses", ""))
    found = {name: sorted(_labels(sections.get(name, ""))) for name in ("hypotheses", "results", "conclusions")}
    missing = {
        name: sorted(expected - set(found[name]))
        for name in ("results", "conclusions")
    }
    extra = {
        name: sorted(set(found[name]) - expected)
        for name in ("results", "conclusions")
    }
    return {
        "labels": found,
        "missing": missing,
        "extra": extra,
        "consistent": not any(missing.values()) and not any(extra.values()),
    }
