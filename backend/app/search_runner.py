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
* Records are saved as unverified `Source` rows (`_store_results_as_sources`): they are third-party data,
  never verified by retrieval. A record with a DOI is stored once per project, whichever saved search finds it first;
  a record without one is kept per search and left to the dedup step. The row keeps ids, DOI, title, year, a dedup title key and the connector's citation count (M2.13).
* Only the `search_run` task handler calls it, and that task type needs gate G2 (M1.8.1): nothing
  runs a bulk search until a person has approved the search strategy and criteria.

Counts cover this one run. Connector records are persisted as unverified `Source` rows for human
metadata review and screening; verification is never inferred from retrieval. "Duplicates removed" is within the run only (repeated ids, then DOI /
title+year+author matches); removing duplicates across databases needs the stored `results` of all
runs of a project, which M1.7.3 will do from the DOI and work key kept here.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.artifacts import register_artifact
from app.connectors.http import bypass_cache
from app.config import get_settings
from app.connectors.access import is_enabled
from app.connectors.base import Connector, ConnectorError, PaperRecord, SearchRequest
from app.dedupe import doi_key, group_duplicates, work_key
from app.models import SOURCE_ORIGIN_RETRIEVED, Project, ProjectStage, SearchQuery, Source
from app.search_query import BooleanQuery
from app.search_limits import DEFAULT_MAX_RESULTS, HARD_MAX_RESULTS

from app.search_query_adapters import adapt
from app.search_rerun import rerun_refusal

SEARCH_RUN_KIND = "search_run"  # artifact kind for one completed search (X.42)

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
    """Page through results. Returns (records, reported_total, truncated, stopped_early).

    A connector whose `search` is relevance-only may offer `boolean_search`, which applies the
    adapter's Boolean string as written (Semantic Scholar bulk search, M1.5.5); it is used when present.

    A `ConnectorError` on the *first* page means nothing was retrieved: it propagates, so the whole
    search fails and nothing is saved (plan rule 22). A `ConnectorError` on a *later* page (e.g.
    OpenAlex's "API key required for cursor paging" once a key-less caller goes past page 1, X.166)
    keeps the records already collected instead of discarding them: `truncated` is set and
    `stopped_early` names why, so the caller can record it as a caveat.
    """
    search = getattr(connector, "boolean_search", None) or connector.search
    records: list[PaperRecord] = []
    cursor: str | None = None
    total: int | None = None
    page_number = 0
    while True:
        page_number += 1
        try:
            page = search(
                SearchRequest(query=text, limit=min(PAGE_SIZE, max_results - len(records)), cursor=cursor, filters=filters)
            )
        except ConnectorError as exc:
            if page_number == 1:
                raise
            return records, total, True, f"{connector.name} stopped paging after page {page_number - 1}: {exc}"
        if total is None:
            total = page.total
        records.extend(page.records)
        if len(records) > max_results:  # a page may hold more than asked for (S2 bulk pages hold 1,000)
            return records[:max_results], total, True, None
        if not page.records or page.next_cursor is None:
            return records, total, bool(getattr(connector, "last_search_capped", False)), None
        if len(records) >= max_results:
            return records[:max_results], total, True, None
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
        known = [c for i in group if (c := distinct[i].cited_by_count) is not None]
        results.append(
            {
                "id": f"{anchor.connector}:{anchor.external_id}",
                "doi": doi_key(anchor.doi),
                "work_key": work_key(anchor),
                "title": anchor.title,
                "authors": list(anchor.authors),
                "year": anchor.year,
                "venue": anchor.venue,
                "abstract": anchor.abstract,
                "url": anchor.url,
                "oa_url": anchor.oa_url,
                "source_ids": dict(anchor.source_ids),
                "quality_flags": list(anchor.quality_flags),
                "language": anchor.language,
                # Highest count among the merged duplicates (M2.13); None = no record had one, not 0.
                "cited_by_count": max(known) if known else None,
            }
        )
    counts = {
        "retrieved": len(records),
        "repeated_ids": len(records) - len(distinct),
        "unique": len(groups),
        "duplicates_removed": len(records) - len(groups),
    }
    return results, counts


def _ingest_key(search_id: uuid.UUID, result: dict[str, Any]) -> str:
    """Stable, bounded idempotency key for one stored result, unique per project.

    A record with a DOI is keyed on the DOI, so the same paper found by two saved searches is one
    `Source`; the first search that finds it owns the row. A record without a DOI is keyed on its
    search and result id, so a retried task still stores nothing twice.
    """
    doi = doi_key(result.get("doi"))
    if doi is not None:
        return "doi:" + hashlib.sha256(doi.encode("utf-8")).hexdigest()[:40]
    digest = hashlib.sha256(f"{search_id}:{result['id']}".encode("utf-8")).hexdigest()[:24]
    return f"search:{search_id}:{digest}"


def _store_results_as_sources(
    db: Session, *, project: Project, actor: str, search_id: uuid.UUID, connector: str, results: list[dict[str, Any]], run_at: datetime
) -> int:
    """Persist completed search records as unverified sources, idempotently for a task retry."""
    created = 0
    for result in results:
        key = _ingest_key(search_id, result)
        if db.scalar(select(Source.id).where(Source.project_id == project.id, Source.ingest_key == key)) is not None:
            continue
        citation_count = result.get("cited_by_count")
        db.add(
            Source(
                project_id=project.id,
                title=result["title"][:500], doi=result.get("doi"), url=result.get("url"),
                authors=result.get("authors") or None, year=result.get("year"), venue=result.get("venue"),
                abstract=result.get("abstract"), oa_url=result.get("oa_url"), source_ids=result.get("source_ids") or None,
                quality_flags=result.get("quality_flags") or None, language=result.get("language"),
                origin=SOURCE_ORIGIN_RETRIEVED, metadata_verified=False, citation_count=citation_count,
                citation_count_source=connector if citation_count is not None else None,
                citation_count_at=run_at if citation_count is not None else None, created_by=actor, ingest_key=key,
                found_via={"method": "database_search", "search_id": str(search_id), "connector": connector},
            )
        )
        created += 1
    return created


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
        with bypass_cache():  # a search is always live, never answered from the connector cache (M1.3.7)
            records, total, truncated, stopped_early = _collect(connector, text, filters, max_results)
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
    counts["cache_bypassed"] = True
    counts["skipped_records"] = int(getattr(connector, "skipped_records", 0) or 0)
    run_at = datetime.now(timezone.utc)
    if stopped_early:
        caveats = [*caveats, stopped_early]
    row = SearchQuery(
        project_id=project.id,
        search_id=search_id,
        database=connector.name,
        query_string=text,
        filters=filters,
        run_at=run_at,
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
    stored_sources = _store_results_as_sources(
        db, project=project, actor=actor, search_id=search_id, connector=connector.name, results=results, run_at=run_at
    )
    db.flush()
    register_artifact(db, project.id, SEARCH_RUN_KIND, str(row.id), ProjectStage.retrieved, actor=actor)
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
            "counts": counts, "sources_created": stored_sources,
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
    refusal = rerun_refusal(latest)
    if refusal:
        raise SearchError(refusal)
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
