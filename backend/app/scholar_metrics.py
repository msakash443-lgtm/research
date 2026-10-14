"""Scholar-outcome metrics (X.20; spec 11 "Scholar outcomes").

Pure functions: plain data in, plain data out. The router (`routers/scholar_metrics.py`) reads the rows and
passes them here. Anything the app does not record yet is reported as `not_recorded` with the reason, never
as a zero or a guess.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.suggestions import DISCARDED, EDITED_AND_ACCEPTED, ACCEPTED

REVIEWED_KINDS = frozenset({ACCEPTED, EDITED_AND_ACCEPTED, DISCARDED})
CORRECTION_KINDS = frozenset({EDITED_AND_ACCEPTED, DISCARDED})


def corrections_per_100(reviewed: int, corrected: int) -> float | None:
    """Human corrections per 100 AI outputs a person has decided on. None when nothing has been decided yet."""
    if reviewed == 0:
        return None
    return round(100 * corrected / reviewed, 1)


def suggestion_review_counts(event_kinds_per_suggestion: Iterable[Iterable[str]]) -> dict[str, int]:
    """Count AI outputs a person has decided on, and how many of those were corrected.

    A correction is an accepted-after-edit or a discard. Each suggestion counts once, however many events it has.
    Suggestions with no decision yet are not counted: they have nothing to measure.
    """
    reviewed = corrected = 0
    for kinds in event_kinds_per_suggestion:
        kind_set = set(kinds)
        if not kind_set & REVIEWED_KINDS:
            continue
        reviewed += 1
        if kind_set & CORRECTION_KINDS:
            corrected += 1
    return {"reviewed": reviewed, "corrected": corrected}


def readiness_summary(sections: Iterable[tuple[str, bool]]) -> dict[str, Any]:
    """Manuscript readiness from `(section title, has a saved version)` pairs.

    Per-section approval (G10) is not recorded anywhere yet, so the checklist reports how many sections are
    drafted and marks approval as not recorded instead of counting it.
    """
    total = drafted = 0
    for _title, has_text in sections:
        total += 1
        if has_text:
            drafted += 1
    return {
        "sections_total": total,
        "sections_drafted": drafted,
        "sections_approved": None,
        "status": "not_recorded",
        "reason": "Per-section approval (gate G10) is not recorded yet, so readiness can't be completed.",
    }


TIME_SAVED_NOT_RECORDED: dict[str, str] = {
    "status": "not_recorded",
    "reason": "The app does not record how long each phase takes. A baseline (manual) and an assisted time per phase are needed first.",
}


def scholar_metrics(
    event_kinds_per_suggestion: Iterable[Iterable[str]],
    sections: Iterable[tuple[str, bool]],
) -> dict[str, Any]:
    counts = suggestion_review_counts(event_kinds_per_suggestion)
    return {
        "human_corrections": {
            "reviewed_ai_outputs": counts["reviewed"],
            "corrected_ai_outputs": counts["corrected"],
            "per_100_ai_outputs": corrections_per_100(counts["reviewed"], counts["corrected"]),
            "definition": "Corrections are edited-then-accepted or discarded AI outputs, per 100 AI outputs a person has decided on.",
        },
        "time_saved": dict(TIME_SAVED_NOT_RECORDED),
        "manuscript_readiness": readiness_summary(sections),
    }
