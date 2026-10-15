"""Rank and filter by citation count (M2.13).

Used for project sources (`GET /sources/by-citations`) and one search run's results
(`GET /searches/{id}/results`). Rules:

* A missing count is "unknown", never 0 (plan rule 22). Unknown items are never hidden by the
  threshold: they stay in the list, flagged `citation_count_unknown`, after every counted item.
* Items with a known count below the threshold are hidden, and the response says how many.
* Counts come from third-party services and differ between them; they rank, they never decide
  inclusion. Screening stays a person's decision.
* The threshold is the request's `min_citations` when given, else the researcher brief's (M2.12).
"""

from __future__ import annotations

from typing import Any, Callable, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ResearchBrief

T = TypeVar("T")


def resolve_threshold(db: Session, project_id: Any, requested: int | None) -> tuple[int | None, str | None]:
    """The threshold to apply and where it came from: "request", "brief" or None (no threshold)."""
    if requested is not None:
        return requested, "request"
    brief_value = db.scalar(select(ResearchBrief.min_citations).where(ResearchBrief.project_id == project_id))
    return (brief_value, "brief") if brief_value is not None else (None, None)


def rank(
    items: Sequence[T], count_of: Callable[[T], int | None], threshold: int | None, threshold_from: str | None
) -> tuple[list[tuple[T, bool]], dict[str, Any]]:
    """Highest count first, unknown counts last; the input order breaks ties (sort is stable).

    Returns `(item, unknown)` pairs that remain after the threshold, plus a summary of what was shown and hidden.
    """
    known = [i for i in items if count_of(i) is not None]
    unknown = [i for i in items if count_of(i) is None]
    known.sort(key=lambda i: count_of(i), reverse=True)  # type: ignore[arg-type, return-value]
    kept = [i for i in known if threshold is None or count_of(i) >= threshold]  # type: ignore[operator]
    shown = [(i, False) for i in kept] + [(i, True) for i in unknown]
    summary = {
        "min_citations": threshold,
        "threshold_from": threshold_from,
        "total": len(items),
        "shown": len(shown),
        "hidden_below_threshold": len(known) - len(kept),
        "unknown_count": len(unknown),
    }
    return shown, summary
