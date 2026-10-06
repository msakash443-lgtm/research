"""Run a database search and keep a re-runnable record of it (spec 5.2.5, 4 SearchQuery).

`run_search` sends a `BooleanQuery` to one connector and stores a `SearchQuery` row: the exact
string sent, the filters, when it ran, how it was counted and which records came back.
`rerun_search` repeats a stored search with the **same string and filters** as the next version,
so two runs can be compared (M1.14.1).

Rules this module keeps:

* Nothing is saved from a failed search (plan rule 22). A connector failure raises `SearchFailed`
  after an audit event; no half-counted row exists. The event is added to the caller's session, so
  the caller must commit it (a rollback loses it) - an API handler should commit, then re-raise.
* Hitting the result cap is recorded (`counts["truncated"]`); a capped search is never presented as
  complete.
* A connector is used only if it is enabled in settings (default off, plan rule 18).
* Records are not saved as `Source` rows here: they are unverified third-party data. Storing them
  is M1.6.4. The row keeps only ids, DOI and a title key.
* Only the `search_run` task handler calls it, and that task type needs gate G2 (M1.8.1): nothing
  runs a bulk search until a person has approved the search strategy and criteria.

Counts cover this one run. "Duplicates removed" is within the run only (repeated ids, then DOI /
title+year+author matches); removing duplicates across databases needs the stored `results` of all
runs of a project, which M1.7.3 will do from the DOI and work key kept here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.connectors.access import is_enabled
from app.connectors.base import Connector, ConnectorError, PaperRecord, SearchRequest
from app.dedupe import doi_key, group_duplicates, work_key
from app.models import Project, SearchQuery
from app.search_query import BooleanQuery
from app.search_limits import DEFAULT_MAX_RESULTS, HARD_MAX_RESULTS
from app.search_query_adapters import adapt

PAGE_SIZE = 100


class SearchError(ValueError):
    """The search could not be started (bad arguments, disabled connector, unknown search)."""


class SearchFailed(RuntimeError):
    """The connector failed while searching; nothing was saved."""


def _check(connector: Connector, max_results: int) -> None:
    if not 1 <= max_results <= HARD_MAX_RESULTS:
        raise SearchError(f"max_results must be between 1 and {HARD_MAX_RESULTS}")
    settings = get_settings()
    if not is_enabled(connector.name, settings.connectors_enabled, settings.connectors_allow_scraping):
        raise SearchError(f"Connector '{connector.name}' is not enabled")


def _collect(connector: Connector, text: str, filters: dict[str, Any], max_results: int):
    """Page through results. Returns (records, reported_total, truncated)."""
    records: list[PaperRecord] = []
    cursor: str | None = None
    total: int | None = None
    while True:
        page = connector.search(
            SearchRequest(query=text, limit=min(PAGE_SIZE, max_results - len(records)), cursor=cursor, filters=filters)
        )
        if total is None:
            total = page.total
        records.extend(page.records)
        if not page.records or page.next_cursor is None:
            return records, total, False
        if len(records) >= max_results:
            return records[:max_results], total, True
        cursor = page.next_cursor


def _summarise(records: list[PaperRecord]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    seen: set[str] = set()
    distinct: list[PaperRecord] = []
    for record in records:
        key = f"{record.connector}:{record.external_id}"
        if key not in seen:
            seen.add(key)
            distinct.append(record)
    groups = group_duplicates(distinct)
    results = []
    for group in groups:
        anchor = distinct[group[0]]
        results.append(
            {
                "id": f"{anchor.connector}:{anchor.external_id}",
                "doi": doi_key(anchor.doi),
                "work_key": work_key(anchor),
            }
        )
    counts = {
        "retrieved": len(records),
        "repeated_ids": len(records) - len(distinct),
        "unique": len(groups),
        "duplicates_removed": len(records) - len(groups),
    }
    return results, counts


def _execute(
    db: Session,
    *,
    project: Project,
    actor: str,
    connector: Connector,
    text: str,
    filters: dict[str, Any],
    exact: bool | None,
    caveats: list[str],
    search_id: uuid.UUID,
    version: int,
    max_results: int,
) -> SearchQuery:
    try:
        records, total, truncated = _collect(connector, text, filters, max_results)
    except ConnectorError as exc:
        audit.record(
            db,
            actor=actor,
            action="search.failed",
            project_id=project.id,
            payload={"search_id": str(search_id), "version": version, "database": connector.name, "error": type(exc).__name__},
        )
        raise SearchFailed(f"{connector.name} search failed: {exc}") from exc
    results, counts = _summarise(records)
    counts["reported_total"] = total
    counts["truncated"] = truncated
    counts["skipped_records"] = int(getattr(connector, "skipped_records", 0) or 0)
    row = SearchQuery(
        project_id=project.id,
        search_id=search_id,
        database=connector.name,
        query_string=text,
        filters=filters,
        run_at=datetime.now(timezone.utc),
        n_results=counts["unique"],
        version=version,
        exact=exact,
        caveats=caveats,
        counts=counts,
        results=results,
        created_by=actor,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action="search.run",
        project_id=project.id,
        payload={
            "search_id": str(search_id),
            "version": version,
            "database": connector.name,
            "exact": exact,
            "counts": counts,
        },
    )
    return row


def run_search(
    db: Session,
    *,
    project: Project,
    actor: str,
    connector: Connector,
    query: BooleanQuery,
    filters: dict[str, Any] | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    search_id: uuid.UUID | None = None,
) -> SearchQuery:
    """Run a new search (version 1). A caller that queued the search passes the `search_id` it handed out."""
    _check(connector, max_results)
    adapted = adapt(query, connector.name)
    return _execute(
        db,
        project=project,
        actor=actor,
        connector=connector,
        text=adapted.text,
        filters=dict(filters or {}),
        exact=adapted.exact,
        caveats=list(adapted.caveats),
        search_id=search_id or uuid.uuid4(),
        version=1,
        max_results=max_results,
    )


def rerun_search(
    db: Session,
    *,
    project: Project,
    actor: str,
    connector: Connector,
    search_id: uuid.UUID,
    max_results: int = DEFAULT_MAX_RESULTS,
    version: int | None = None,
) -> SearchQuery:
    """Repeat a stored search, unchanged, as its next version (or as `version`, which a queued re-run fixed up front)."""
    _check(connector, max_results)
    latest = db.scalar(
        select(SearchQuery)
        .where(SearchQuery.project_id == project.id, SearchQuery.search_id == search_id)
        .order_by(SearchQuery.version.desc())
        .limit(1)
    )
    if latest is None:
        raise SearchError("Unknown search")
    if latest.database != connector.name:
        raise SearchError(f"Search was run on '{latest.database}', not '{connector.name}'")
    return _execute(
        db,
        project=project,
        actor=actor,
        connector=connector,
        text=latest.query_string,
        filters=dict(latest.filters or {}),
        exact=latest.exact,
        caveats=list(latest.caveats or []),
        search_id=search_id,
        version=version or latest.version + 1,
        max_results=max_results,
    )


def diff_runs(older: SearchQuery, newer: SearchQuery) -> dict[str, list[str]]:
    """Which record ids appeared / disappeared between two runs of the same search."""
    if older.search_id != newer.search_id:
        raise SearchError("Runs belong to different searches")
    before = {r["id"] for r in older.results or []}
    after = {r["id"] for r in newer.results or []}
    return {"added": sorted(after - before), "removed": sorted(before - after)}
