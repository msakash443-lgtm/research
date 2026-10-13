"""PRISMA flow numbers for a project, computed from the search log (spec 5.2.5).

Numbers come from stored search runs, never from a model (plan rule 24):

* Only the **latest version** of each search counts; older versions are superseded re-runs.
* identified = records retrieved by those runs, summed over databases.
* duplicates removed = identified minus the unique records across **all** runs, using the same
  rules as `dedupe` (same DOI, else same title+year+first author; two different DOIs never match).
* A run that hit its cap is listed in `warnings`; its numbers are a lower bound.
* Snowballing (M1.9) is PRISMA 2020's "other methods: citation searching", reported apart from the
  database searches under `other_methods`: works fetched, and new records it added to the project.

Screening counts come from each source's settled decision (`app.screening.settled_decision`): a source that is
unscreened, awaiting a second reviewer or in dual-screen conflict counts as unscreened, never as excluded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, SearchQuery, SnowballRun
from app.config import get_settings
from app.screening import STAGES, audit_statuses_by_decision, autoexclude_threshold, check_reason, criterion_codes, dual_screen_status, effective_ai_exclusion, rows_for_stage, screenable_sources, settled_decision


def latest_runs(db: Session, project: Project) -> list[SearchQuery]:
    rows = db.scalars(
        select(SearchQuery)
        .where(SearchQuery.project_id == project.id, SearchQuery.counts.is_not(None))
        .order_by(SearchQuery.run_at, SearchQuery.version)
    ).all()
    latest: dict[Any, SearchQuery] = {}
    for row in rows:
        if row.search_id not in latest or row.version > latest[row.search_id].version:
            latest[row.search_id] = row
    return sorted(latest.values(), key=lambda r: (r.run_at, str(r.search_id)))


def _unique_across(runs: list[SearchQuery]) -> int:
    groups: list[str | None] = []  # DOI held by each group, if any
    by_doi: dict[str, int] = {}
    by_key: dict[str, int] = {}
    for run in runs:
        for entry in run.results or []:
            doi, key = entry.get("doi"), entry.get("work_key")
            target = by_doi.get(doi) if doi else None
            if target is None and key is not None and key in by_key:
                candidate = by_key[key]
                if not (doi and groups[candidate] and groups[candidate] != doi):
                    target = candidate
            if target is None:
                groups.append(doi)
                target = len(groups) - 1
            elif doi and groups[target] is None:
                groups[target] = doi
            if doi:
                by_doi.setdefault(doi, target)
            if key is not None:
                by_key.setdefault(key, target)
    return len(groups)


def _snowball_runs(db: Session, project: Project) -> list[SnowballRun]:
    return list(db.scalars(select(SnowballRun).where(SnowballRun.project_id == project.id).order_by(SnowballRun.run_at, SnowballRun.id)))


def _citation_searching(db: Session, project: Project) -> dict[str, Any]:
    runs = _snowball_runs(db, project)
    return {
        "citation_searching": {
            "runs": len(runs),
            "identified": sum(int(r.counts.get("fetched") or 0) for r in runs),
            "added": sum(int(r.counts.get("added") or 0) for r in runs),
            "already_in_project": sum(int(r.counts.get("already_in_project") or 0) for r in runs),
        }
    }


def _screening_flow(db: Session, project: Project) -> dict[str, Any]:
    """Screening counts for the PRISMA flow, from each source's settled decision (`screening.settled_decision`).

    A source with no settled decision (unscreened, awaiting a second reviewer, or in dual-screen conflict) is
    counted as unscreened or conflicting, never as excluded. A single-reviewer source whose only decision is an AI
    auto-exclude that the policy currently honours (`screening.effective_ai_exclusion`) is counted as excluded, as the queue does.
    `exclusion_reasons_missing` counts exclusions whose reason is absent or not one of the project's exclusion codes;
    the included set is not ready to approve until it is 0.
    """
    settings = get_settings()
    threshold = autoexclude_threshold(project, settings.screening_ai_autoexclude_confidence_threshold)
    audit_statuses = audit_statuses_by_decision(db, project) if project.ai_autoexclude_enabled else {}
    codes = criterion_codes(db, project)
    sources = screenable_sources(db, project)
    rows_by_stage = {stage: rows_for_stage(db, project, stage) for stage in STAGES}
    missing = 0
    unscreened = 0
    blocks: dict[str, dict[str, Any]] = {}
    for stage in STAGES:
        excluded_by_reason: dict[str, int] = {}
        block: dict[str, Any] = {"decided": 0, "included": 0, "excluded": 0, "conflicts": 0, "excluded_by_reason": excluded_by_reason}
        for source in sources:
            rows = rows_by_stage[stage].get(source.id, [])
            if project.dual_screen_enabled and dual_screen_status(rows)[0] == "conflict":
                block["conflicts"] += 1
            decision = settled_decision(project, rows)
            if decision is None and not project.dual_screen_enabled:
                decision = effective_ai_exclusion(
                    rows, audit_statuses, enabled=project.ai_autoexclude_enabled, threshold=threshold,
                    prescreen_disabled=project.ai_prescreen_disabled,
                )
            if decision is None:
                if stage == "title_abstract":
                    unscreened += 1
                continue
            block["decided"] += 1
            if decision.decision == "include":
                block["included"] += 1
            elif decision.decision == "exclude":
                block["excluded"] += 1
                if check_reason("exclude", decision.reason_code, codes) is None:
                    excluded_by_reason[decision.reason_code] = excluded_by_reason.get(decision.reason_code, 0) + 1
                else:
                    missing += 1
        blocks[stage] = block
    return {
        "unscreened": unscreened,
        "title_abstract": blocks["title_abstract"],
        "full_text": blocks["full_text"],
        "exclusion_reasons_missing": missing,
        "exclusion_reasons_complete": missing == 0,
    }


def flow_counts(db: Session, project: Project) -> dict[str, Any]:
    runs = latest_runs(db, project)
    identified = sum(r.counts["retrieved"] for r in runs)
    unique = _unique_across(runs)
    warnings = [
        f"{r.database} search {r.version} stopped at its result cap; its numbers are a lower bound"
        for r in runs
        if r.counts.get("truncated")
    ] + [
        f"snowball run {r.id} stopped at its cap of {r.caps.get('max_new')} new records; its numbers are a lower bound"
        for r in _snowball_runs(db, project)
        if r.counts.get("capped")
    ]
    return {
        "identification": {
            "searches": [
                {
                    "search_id": str(r.search_id),
                    "database": r.database,
                    "version": r.version,
                    "run_at": r.run_at,
                    "identified": r.counts["retrieved"],
                    "truncated": bool(r.counts.get("truncated")),
                    "exact": r.exact,
                }
                for r in runs
            ],
            "identified": identified,
            "duplicates_removed": identified - unique,
            "after_duplicates_removed": unique,
        },
        "other_methods": _citation_searching(db, project),
        "screening": _screening_flow(db, project),
        "warnings": warnings,
    }
