"""Pure bibliometric aggregations (spec 5.5.6, plan M3.12)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable


def publication_trends(years: Iterable[int | None]) -> dict[str, object]:
    """Count non-merged source publication years; keep missing years visible."""
    counts: Counter[int] = Counter()
    unknown_year = 0
    total_sources = 0
    for year in years:
        total_sources += 1
        if year is None:
            unknown_year += 1
        else:
            counts[year] += 1

    return {
        "by_year": [{"year": year, "count": counts[year]} for year in sorted(counts)],
        "total_sources": total_sources,
        "unknown_year": unknown_year,
        "screening_filter": "none",
        "note": "Includes all non-merged sources regardless of screening status; sources without a year are counted as unknown.",
    }


__all__ = ["publication_trends"]
