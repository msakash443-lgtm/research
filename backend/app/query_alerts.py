"""Saved-query alerts (spec 5.2.4, plan M1.12): re-run a saved search on a schedule, surface new papers.

A subscription (``SearchAlert``, one per saved search) re-runs the stored query unchanged as its
next version (M1.7.2 ``rerun_search``). A re-run is bulk retrieval, so the ``query_alert`` task it
enqueues **requires gate G2**, exactly like a queued search run (M1.8.1) — a person approved the
query once, and the same approval covers its scheduled re-runs.

Scheduling: the worker loop sweeps alerts whose ``next_run_at`` is due and enqueues one task per
due slot. The sweep advances ``next_run_at`` to the next slot and enqueues with a per-slot
idempotency key, so a crashed or repeated sweep can never enqueue the same slot twice. A run that
crashed after the search version was saved but before the alert was updated simply re-runs as one
more version; the idempotency guard (``last_run_at >= slot``) stops a *completed* run from running
again.

"New" = the new version's records minus the union of **every earlier version** of that search, so
a paper that left and came back is not re-surfaced. Surfacing = the new version row + the alert's
last-run state + audit events (ids/DOIs, capped) — alert results are not saved as ``Source`` rows
(the M1.7.2/M1.6.4 line) and no email/calendar is sent (spec §11 notification services are out of
scope for v1).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.models import Project, SearchAlert, SearchQuery, utcnow
from app.search_rerun import rerun_refusal
from app.task_queue import enqueue_task

QUERY_ALERT = "query_alert"
DEFAULT_INTERVAL_SECONDS = 24 * 3600
MIN_INTERVAL_SECONDS = 3600
MAX_INTERVAL_SECONDS = 30 * 24 * 3600
NEW_RESULTS_CAP = 50


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes (Postgres keeps the offset): normalise before comparing
    against an aware `utcnow()`."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class AlertError(Exception):
    """A state problem a retry cannot fix (bad interval, already subscribed, search can't re-run)."""


def latest_search(db: Session, project: Project, search_id: uuid.UUID) -> SearchQuery | None:
    return db.scalar(
        select(SearchQuery)
        .where(SearchQuery.project_id == project.id, SearchQuery.search_id == search_id)
        .order_by(SearchQuery.version.desc())
        .limit(1)
    )


def create_alert(
    db: Session,
    project: Project,
    search_id: uuid.UUID,
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    actor: str,
) -> SearchAlert:
    """Subscribe to a saved search. Raises `AlertError` with a plain-language reason (the API
    maps it to 409); the caller commits."""
    if not MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS:
        raise AlertError("The alert interval must be between 1 hour and 30 days.")
    latest = latest_search(db, project, search_id)
    if latest is None or latest.run_at is None:
        raise AlertError("This search has not been run yet, so there is nothing to re-run.")
    refusal = rerun_refusal(latest)
    if refusal:
        raise AlertError(refusal)
    if db.scalar(select(SearchAlert.id).where(SearchAlert.search_id == search_id)) is not None:
        raise AlertError("This search already has an alert subscription.")
    now = utcnow()
    alert = SearchAlert(
        project_id=project.id,
        search_id=search_id,
        interval_seconds=interval_seconds,
        # the first check is one interval out: the person has just seen the latest results
        next_run_at=now + timedelta(seconds=interval_seconds),
        enabled=True,
        created_by=actor,
    )
    db.add(alert)
    audit.record(
        db, actor=actor, action="query_alert.created", project_id=project.id,
        payload={
            "alert_id": str(alert.id), "search_id": str(search_id),
            "interval_seconds": interval_seconds, "next_run_at": alert.next_run_at.isoformat(),
        },
    )
    return alert


def sweep_due_alerts(db: Session, *, now: datetime | None = None) -> int:
    """Enqueue one `query_alert` task per due alert and advance it to its next slot. The caller
    commits. Returns how many tasks were enqueued."""
    now = now or utcnow()
    due = db.scalars(select(SearchAlert).where(
        SearchAlert.enabled.is_(True), SearchAlert.next_run_at <= now)).all()
    enqueued = 0
    for alert in due:
        slot = now + timedelta(seconds=alert.interval_seconds)
        alert.next_run_at = slot  # this slot is now claimed; a re-sweep won't pick it up again
        enqueue_task(
            db,
            alert.project_id,
            QUERY_ALERT,
            {
                "project_id": str(alert.project_id), "alert_id": str(alert.id),
                # the enqueue time is what a retried run compares against: a run already completed
                # at or after it (a crash between the commit and the ack) must not run again
                "enqueued_at": now.isoformat(), "actor": audit.SYSTEM_WORKER,
            },
            actor=audit.SYSTEM_WORKER,
            idempotency_key=f"query-alert:{alert.id}:{slot.isoformat()}",
        )
        enqueued += 1
    return enqueued


def _record_keys(entries: list | None) -> list[tuple[str, str | None]]:
    """(identity, doi) per result entry: identity is the work key when present, else the id."""
    out = []
    for entry in entries or []:
        entry = entry or {}
        key = entry.get("work_key") or entry.get("id")
        if key:
            out.append((str(key), entry.get("doi")))
    return out


def new_records(db: Session, project: Project, search_id: uuid.UUID, version: int) -> list[dict]:
    """The records of the search's `version` that appear in no other version (ids/DOIs only)."""
    row = db.scalar(select(SearchQuery).where(
        SearchQuery.project_id == project.id, SearchQuery.search_id == search_id,
        SearchQuery.version == version))
    if row is None:
        return []
    past = db.scalars(select(SearchQuery).where(
        SearchQuery.project_id == project.id, SearchQuery.search_id == search_id,
        SearchQuery.id != row.id)).all()
    seen = set()
    for r in past:
        seen.update(key for key, _ in _record_keys(r.results))
    external = {}
    for entry in row.results or []:
        entry = entry or {}
        key = entry.get("work_key") or entry.get("id")
        if key:
            external[str(key)] = entry.get("id")
    return [
        {"id": external[key], "doi": doi}
        for key, doi in _record_keys(row.results)
        if key not in seen
    ]


def record_alert_run(db: Session, project: Project, alert: SearchAlert, row: SearchQuery, *, actor: str) -> dict:
    """Record a completed alert re-run: what is new since the earlier versions, the alert's
    last-run state, and the audit trail. The caller (the G2-gated task handler) ran the search
    and commits the session. Nothing here re-runs a search (M1.8.1 line: only the gated handler
    touches the search runner)."""
    new = new_records(db, project, alert.search_id, row.version)
    alert.last_run_at = row.run_at or utcnow()
    alert.last_run_version = row.version
    alert.last_new = len(new)
    audit.record(
        db, actor=actor, action="query_alert.checked", project_id=project.id,
        payload={
            "alert_id": str(alert.id), "search_id": str(alert.search_id),
            "version": row.version, "n_results": row.n_results, "new": len(new),
        },
    )
    if new:
        audit.record(
            db, actor=actor, action="query_alert.new", project_id=project.id,
            payload={
                "alert_id": str(alert.id), "search_id": str(alert.search_id),
                "version": row.version, "new": new[:NEW_RESULTS_CAP],
            },
        )
    project.touch()
    return {"version": row.version, "new": len(new)}
