"""Durable database-backed worker.

Run this as a separate process in Docker/production. It claims tasks from the task queue
(app.task_queue) and runs the registered handler for each (app.task_handlers). It does
not execute arbitrary user code or visit user-supplied URLs.

`claim_next_run` and `reap_stale_runs` below are the pre-queue way of running research
runs. The loop no longer calls them (runs are tasks now); they are kept only until the
queue port has been reviewed, then removed (plan M0.6.6).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from app import audit
from app.config import get_settings
from app.database import SessionLocal
from app.models import ResearchRun, ResearchRunStatus, utcnow
from app.gates import release_tasks_for_approved_gates
from app.task_queue import reap_expired_tasks
from app.task_runner import run_one_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def claim_next_run() -> str | None:
    db = SessionLocal()
    try:
        statement = (
            select(ResearchRun)
            .where(ResearchRun.status == ResearchRunStatus.queued)
            .order_by(ResearchRun.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        run = db.scalar(statement)
        if run is None:
            return None
        run.status = ResearchRunStatus.running
        run.started_at = utcnow()
        db.commit()
        return str(run.id)
    except Exception:
        db.rollback()
        logger.exception("Unable to claim a research run")
        return None
    finally:
        db.close()


def reap_stale_runs(now: datetime | None = None) -> int:
    """Recover runs left `running` by a crashed worker.

    A run whose lease (`research_run_lease_seconds` since `started_at`) has expired is
    requeued, or marked failed once `research_run_max_attempts` is reached. Failing loudly
    keeps a poisoned run from looping forever.
    """
    settings = get_settings()
    now = now or utcnow()
    cutoff = now - timedelta(seconds=settings.research_run_lease_seconds)
    db = SessionLocal()
    try:
        stale = db.scalars(
            select(ResearchRun)
            .where(ResearchRun.status == ResearchRunStatus.running, ResearchRun.started_at < cutoff)
            .with_for_update(skip_locked=True)
        ).all()
        for run in stale:
            if run.attempt_count >= settings.research_run_max_attempts:
                run.status = ResearchRunStatus.failed
                run.error_message = (
                    f"The run stopped responding and was abandoned after {run.attempt_count} attempts. "
                    "Check the worker logs, then submit the question again."
                )
                run.completed_at = now
                audit.record(
                    db, actor=audit.SYSTEM_WORKER, action="research_run.abandoned", project_id=run.project_id,
                    payload={"run_id": str(run.id), "attempts": run.attempt_count},
                )
                logger.warning("Research run %s failed after %s attempts", run.id, run.attempt_count)
            else:
                run.status = ResearchRunStatus.queued
                run.started_at = None
                audit.record(
                    db, actor=audit.SYSTEM_WORKER, action="research_run.requeued", project_id=run.project_id,
                    payload={"run_id": str(run.id), "attempts": run.attempt_count},
                )
                logger.warning("Requeued stale research run %s (attempt %s)", run.id, run.attempt_count)
        db.commit()
        return len(stale)
    except Exception:
        db.rollback()
        logger.exception("Unable to reap stale research runs")
        return 0
    finally:
        db.close()


def release_gated_tasks() -> int:
    """Queue blocked tasks whose gate a person has already approved (heals an approval/enqueue race)."""
    db = SessionLocal()
    try:
        released = release_tasks_for_approved_gates(db)
        db.commit()
        return released
    except Exception:
        db.rollback()
        logger.exception("Unable to release gated tasks")
        return 0
    finally:
        db.close()


def run_forever() -> None:
    settings = get_settings()
    logger.info("Worker started")
    while True:
        reap_expired_tasks()
        release_gated_tasks()
        if not run_one_task():
            time.sleep(settings.research_worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
