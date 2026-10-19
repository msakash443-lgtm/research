"""Resolve a DOI-only or title-only own-paper request through the enabled lookup connectors (M2.14.1).

Used by the `own_paper_lookup` task (`app/task_handlers.py`), never run inline in the request: the
connector calls can be slow or rate-limited, so the endpoint (`routers/sources.py`) queues the task
and returns 202 at once; a `OwnPaperLookup` row is written once the task has resolved (or failed to
resolve) the match.

A DOI lookup asks `get_by_id` on each enabled connector, in the same service order the citation
verifier uses (`app.connectors.factory.LOOKUP`); the first hit wins. A title-only lookup searches
every enabled connector and keeps the closest title match, using the same similarity bar
(`app.citation_verifier.TITLE_SIMILARITY`) the citation verifier applies to an existing source's
title - never a fuzzy guess below it. Either way, a connector that fails outright (network, bad
response) is distinguished from one that cleanly answers "I don't know this work": only the latter,
from every connector asked, is a genuine `not_found`; any real connector error makes the attempt
retryable instead of a false terminal "not found".
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Sequence

from app.connectors.base import Connector, ConnectorError, NotSupportedError, PaperRecord, SearchRequest
from app.dedupe import normalise_title

TITLE_SIMILARITY = 0.92  # same bar as app.citation_verifier.TITLE_SIMILARITY
SEARCH_LIMIT = 5


@dataclass(frozen=True)
class LookupResult:
    record: PaperRecord | None
    connector: str | None
    had_error: bool  # a connector failed outright; None record here means "retry", not "not found"


def _title_score(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise_title(a), normalise_title(b)).ratio()


def resolve_by_doi(doi: str, connectors: Sequence[Connector]) -> LookupResult:
    had_error = False
    for conn in connectors:
        try:
            record = conn.get_by_id(doi)
        except NotSupportedError:
            continue
        except ConnectorError:
            had_error = True
            continue
        if record is not None:
            return LookupResult(record, conn.name, False)
    return LookupResult(None, None, had_error)


def resolve_by_title(title: str, connectors: Sequence[Connector]) -> LookupResult:
    best: PaperRecord | None = None
    best_name: str | None = None
    best_score = 0.0
    had_error = False
    for conn in connectors:
        try:
            page = conn.search(SearchRequest(query=title, limit=SEARCH_LIMIT))
        except NotSupportedError:
            continue
        except ConnectorError:
            had_error = True
            continue
        for record in page.records:
            score = _title_score(title, record.title)
            if score > best_score:
                best, best_name, best_score = record, conn.name, score
    if best is not None and best_score >= TITLE_SIMILARITY:
        return LookupResult(best, best_name, False)
    return LookupResult(None, None, had_error)


def record_to_source_fields(record: PaperRecord) -> dict:
    """`PaperRecord` fields mapped onto `SourceCreate`'s own-paper fields (M2.14.1)."""
    return {
        "title": record.title,
        "doi": record.doi,
        "url": record.url,
        "authors": list(record.authors) or None,
        "year": record.year,
        "venue": record.venue,
        "abstract": record.abstract,
        "oa_url": record.oa_url,
    }
