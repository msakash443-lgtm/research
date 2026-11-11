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
from typing import Callable

from app.config import get_settings
from app.database import SessionLocal
from app.gates import release_tasks_for_approved_gates
from app.models import utcnow
from app.query_alerts import sweep_due_alerts
from app.privacy_profile import effective_privacy
from app.retention import purge_expired_run_snapshots
from app.task_queue import reap_expired_tasks
from app.logging_config import configure_logging
from app.task_runner import run_one_task

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


def _run_step(name: str, step: Callable[[], object]) -> object:
    """Run one step of a worker pass. A failure is logged and returns None, so a broken sweep
    cannot stop the steps after it (in particular the task claim) from running."""
    try:
        return step()
    except Exception:
        logger.exception("Worker step %r failed; continuing", name)
        return None


def _reaper_loop(stop: threading.Event, interval: float) -> None:
    """Reap tasks whose lease has lapsed, on a thread of its own (X.115). The claim loop blocks while a
    handler runs, so a hung handler would otherwise stop the reaping and leave its task `running` for ever."""
    while not stop.is_set():
        _run_step("reap expired tasks", reap_expired_tasks)
        stop.wait(interval)


def run_forever(stop: threading.Event | None = None) -> None:
    """Claim and run tasks until `stop` is set. Each step of a pass is isolated: a database error
    is logged, the step is retried on the next pass, and the worker process never ends."""
    stop = stop if stop is not None else threading.Event()
    settings = get_settings()
    logger.info("Worker started")
    reaper = threading.Thread(target=_reaper_loop, args=(stop, settings.research_worker_poll_seconds), name="task-reaper", daemon=True)
    reaper.start()
    while not stop.is_set():
        _run_step("release gated tasks", release_gated_tasks)
        _run_step("sweep due query alerts", sweep_due_query_alerts)
        _run_step("sweep run snapshot retention", sweep_run_snapshot_retention)
        ran = _run_step("run one task", run_one_task)
        if not ran:
            stop.wait(settings.research_worker_poll_seconds)
    reaper.join()
    logger.info("Worker stopped")


if __name__ == "__main__":
    configure_logging()
    stop_event = threading.Event()
    _install_shutdown_handlers(stop_event)
    run_forever(stop_event)
