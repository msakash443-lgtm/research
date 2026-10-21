"""Durable database-backed worker.

Run this as a separate process in Docker/production. It claims tasks from the task queue
(app.task_queue) and runs the registered handler for each (app.task_handlers). It does
not execute arbitrary user code or visit user-supplied URLs.

Research runs are tasks like any other; the old run-level claim/reaper was removed (plan M0.6.6).
"""

from __future__ import annotations

import logging
import signal
import threading

from app.config import get_settings
from app.database import SessionLocal
from app.gates import release_tasks_for_approved_gates
from app.models import utcnow
from app.query_alerts import sweep_due_alerts
from app.privacy_profile import effective_privacy
from app.retention import purge_expired_run_snapshots
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

def sweep_run_snapshot_retention() -> int:
    """Clear expired research-run input snapshots (X.4.7.4). No-op while the setting is unset."""
    days = effective_privacy(get_settings())["run_snapshot_retention_days"]
    if days is None:
        return 0
    db = SessionLocal()
    try:
        cleared = purge_expired_run_snapshots(db, utcnow(), days)
        db.commit()
        return cleared
    except Exception:
        db.rollback()
        logger.exception("Unable to purge expired run snapshots")
        return 0
    finally:
        db.close()


def _install_shutdown_handlers(stop: threading.Event) -> None:
    """A SIGTERM (deploy, `docker stop`) or SIGINT ends the loop once the task in hand finishes."""

    def request_stop(signum, frame):
        logger.info("Stop requested (signal %s); finishing the current task", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def run_forever(stop: threading.Event | None = None) -> None:
    """Claim and run tasks until `stop` is set. A failed pass is logged and retried after a pause,
    so a database error never ends the worker process."""
    stop = stop if stop is not None else threading.Event()
    settings = get_settings()
    logger.info("Worker started")
    while not stop.is_set():
        try:
            reap_expired_tasks()
            release_gated_tasks()
            sweep_due_query_alerts()
            sweep_run_snapshot_retention()
            idle = not run_one_task()
        except Exception:
            logger.exception("Worker pass failed; retrying after a pause")
            idle = True
        if idle:
            stop.wait(settings.research_worker_poll_seconds)
    logger.info("Worker stopped")


if __name__ == "__main__":
    stop_event = threading.Event()
    _install_shutdown_handlers(stop_event)
    run_forever(stop_event)
