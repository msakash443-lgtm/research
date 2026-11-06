"""Rapid landscape scan for the idea brief (M4.7.a, spec 5.1.2): top-cited papers, recent reviews, volume by year.

Pure: it summarises records a connector has already returned for one search. It describes what that search
returned, not the whole literature. Citation counts are as each service reports them; None means the service gave
no count and is never read as zero. Review detection is a title heuristic, so the scholar checks the list.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.connectors.base import PaperRecord
from app.dedupe import group_duplicates

REVIEW_TITLE = re.compile(r"\b(reviews?|meta-analys[ie]s)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ScanEntry:
    connector: str
    external_id: str
    title: str
    year: int | None
    cited_by_count: int | None
    doi: str | None
    url: str | None


@dataclass(frozen=True)
class LandscapeScan:
    sample_size: int
    top_cited_in_sample: tuple[ScanEntry, ...]
    recent_reviews: tuple[ScanEntry, ...]
    volume_by_year_in_sample: dict[int, int]
    undated: int
    uncounted: int
    duplicates_removed: int


def _entry(record: PaperRecord) -> ScanEntry:
    return ScanEntry(
        connector=record.connector,
        external_id=record.external_id,
        title=record.title,
        year=record.year,
        cited_by_count=record.cited_by_count,
        doi=record.doi,
        url=record.url,
    )


def _best_of_each_work(records: list[PaperRecord]) -> list[PaperRecord]:
    """One record per work (same DOI, or same title/year/first author); the most cited copy wins, ties go to the first."""
    kept = []
    for group in group_duplicates(records):
        kept.append(max((records[i] for i in group), key=lambda r: -1 if r.cited_by_count is None else r.cited_by_count))
    return kept


def summarise(records: tuple[PaperRecord, ...] | list[PaperRecord], *, reviews_since: int, top_n: int = 10) -> LandscapeScan:
    """Top-cited, recent reviews, and a count per publication year. Ties sort by external id so output is stable."""
    given = len(records)
    records = _best_of_each_work(list(records))
    counted = [r for r in records if r.cited_by_count is not None]
    top = sorted(counted, key=lambda r: (-r.cited_by_count, r.external_id))[:top_n]
    reviews = sorted(
        (r for r in records if r.year is not None and r.year >= reviews_since and REVIEW_TITLE.search(r.title)),
        key=lambda r: (-r.year, r.title, r.external_id),
    )[:top_n]
    years = Counter(r.year for r in records if r.year is not None)
    return LandscapeScan(
        sample_size=len(records),
        top_cited_in_sample=tuple(_entry(r) for r in top),
        recent_reviews=tuple(_entry(r) for r in reviews),
        volume_by_year_in_sample=dict(sorted(years.items())),
        undated=sum(1 for r in records if r.year is None),
        uncounted=sum(1 for r in records if r.cited_by_count is None),
        duplicates_removed=given - len(records),
    )
