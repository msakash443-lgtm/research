"""Run-snapshot retention (X.4.7.4).

Owner decision (2026-10-19, chat): no retention period is set yet, so by default nothing is purged —
the setting only takes effect once the owner configures `RESEARCH_RUN_SNAPSHOT_RETENTION_DAYS`. When
set, finished research runs older than the period have their `input_snapshot` (the full evidence/prompt
context sent to the model) cleared; the answer, status, timestamps and audit trail are never touched,
so the run stays visible and attributable, just without the raw context that produced it.

Audit-note retention (the other half of X.4.7.4) is not implemented here: `audit_events` is append-only
at the database level (`app/audit_guard.py`, by design, for research-integrity reasons), so no row may be
updated or deleted to redact it. A compliant purge would need a separate design (e.g. a retained,
re-attributed summary written outside the ordinary append-only path) — tracked as a follow-up, not
built as a no-op setting here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import ResearchRun, ResearchRunStatus

FINISHED_STATUSES = (
    ResearchRunStatus.completed,
    ResearchRunStatus.needs_sources,
    ResearchRunStatus.needs_configuration,
    ResearchRunStatus.failed,
)


def snapshot_cutoff(now: datetime, retention_days: int | None) -> datetime | None:
    """The oldest `completed_at` a run's snapshot may keep, or `None` if nothing is purged."""
    if retention_days is None:
        return None
    return now - timedelta(days=retention_days)


def purge_expired_run_snapshots(db: Session, now: datetime, retention_days: int | None) -> int:
    """Clear `input_snapshot` on finished runs completed before the cutoff. Returns the number cleared.

    Caller commits. A no-op (returns 0) when `retention_days` is `None`.
    """
    cutoff = snapshot_cutoff(now, retention_days)
    if cutoff is None:
        return 0
    due_ids = list(
        db.scalars(
            select(ResearchRun.id).where(
                ResearchRun.status.in_(FINISHED_STATUSES),
                ResearchRun.completed_at.is_not(None),
                ResearchRun.completed_at < cutoff,
                ResearchRun.input_snapshot.is_not(None),
            )
        )
    )
    if not due_ids:
        return 0
    db.execute(update(ResearchRun).where(ResearchRun.id.in_(due_ids)).values(input_snapshot=None))
    return len(due_ids)
