"""Durable database-backed worker.

Run this as a separate process in Docker/production. It claims tasks from the task queue
(app.task_queue) and runs the registered handler for each (app.task_handlers). It does
not execute arbitrary user code or visit user-supplied URLs.

Research runs are tasks like any other; the old run-level claim/reaper was removed (plan M0.6.6).
"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.database import SessionLocal
from app.gates import release_tasks_for_approved_gates
from app.query_alerts import sweep_due_alerts
from app.task_queue import reap_expired_tasks
from app.task_runner import run_one_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


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


def sweep_due_query_alerts() -> int:
    """Enqueue due alert re-runs (one task per due slot; idempotent per slot) — plan M1.12."""
    db = SessionLocal()
    try:
        enqueued = sweep_due_alerts(db)
        db.commit()
        return enqueued
    except Exception:
        db.rollback()
        logger.exception("Unable to sweep due query alerts")
        return 0
    finally:
        db.close()


def run_forever() -> None:
    settings = get_settings()
    logger.info("Worker started")
    while True:
        reap_expired_tasks()
        release_gated_tasks()
        sweep_due_query_alerts()
        if not run_one_task():
            time.sleep(settings.research_worker_poll_seconds)


if __name__ == "__main__":
    run_forever()
