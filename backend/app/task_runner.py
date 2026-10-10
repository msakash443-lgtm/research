"""Run one task from the queue: claim, dispatch to its handler, keep the lease alive, report."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime

from app import arxiv_fulltext_task, task_handlers  # noqa: F401  (importing registers the handlers)
from app.config import get_settings
from app.database import SessionLocal
from app.gates import gate_is_approved
from app.task_queue import block_claimed_task, claim_next_task, complete_task, fail_task, heartbeat
from app.task_registry import REQUIRED_GATES, ClaimedTask, PermanentTaskError, TaskOwnershipLost, get_handler

logger = logging.getLogger(__name__)


def _heartbeat_loop(task: ClaimedTask, stop: threading.Event, interval: float) -> None:
    """Renew the lease while the handler runs, so a long task isn't reaped as dead."""
    while not stop.wait(interval):
        if not heartbeat(task.id, task.attempt):
            task.lost.set()  # the handler's next ensure_owned() stops it
            logger.warning("Task %s lost its lease; another worker may have taken it over", task.id)
            return


def run_one_task(now: datetime | None = None, task_id: uuid.UUID | None = None) -> bool:
    """Claim and run a single task (the given one, if `task_id`). Returns False when nothing was due."""
    task = claim_next_task(now, task_id)
    if task is None:
        return False

    gate = REQUIRED_GATES.get(task.type)
    if gate is not None:
        with SessionLocal() as db:
            approved = gate_is_approved(db, task.project_id, gate)
        if not approved:
            # Should have been blocked at enqueue; never run it without a person's approval.
            block_claimed_task(task.id, task.attempt, gate)
            return True

    handler = get_handler(task.type)
    if handler is None:
        fail_task(task.id, task.attempt, f"No handler is registered for task type {task.type!r}.", permanent=True, now=now)
        return True

    stop = threading.Event()
    interval = max(get_settings().task_lease_seconds / 3, 1)
    beat = threading.Thread(target=_heartbeat_loop, args=(task, stop, interval), daemon=True)
    beat.start()
    try:
        handler(task)
    except TaskOwnershipLost:
        stop.set()
        # Another worker owns the task now; it reports the outcome. Report nothing from here.
        logger.warning("Task %s (%s) attempt %s stopped: ownership lost", task.id, task.type, task.attempt)
    except PermanentTaskError as exc:
        stop.set()
        fail_task(task.id, task.attempt, str(exc), permanent=True, now=now)
    except Exception as exc:
        stop.set()
        logger.exception("Task %s (%s) failed on attempt %s", task.id, task.type, task.attempt)
        fail_task(task.id, task.attempt, f"{type(exc).__name__}: {exc}", now=now)
    else:
        stop.set()
        if not complete_task(task.id, task.attempt):
            logger.warning("Task %s (%s) finished but attempt %s no longer owns it", task.id, task.type, task.attempt)
    finally:
        stop.set()
        beat.join(timeout=2)
    return True
