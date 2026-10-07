"""General task queue: claim with a lease, heartbeat, retry with backoff, block and pause.

Each function runs in its own short transaction. A worker that claims a task receives a
`ClaimedTask` carrying the `attempt` number it was given; every later call must pass it
back. If the lease expired and the task was handed to someone else, the old attempt number
no longer matches and the stale worker's heartbeat/complete/fail is refused, so a slow
worker can't overwrite a newer attempt.

Failures are recorded, never papered over: the last error stays on the task, and a task
that runs out of attempts ends `failed` (plan rule 22). Tasks behind a human gate are
`blocked` and are never claimed; releasing them is tied to gate approval (M0.5.4).

On PostgreSQL the claim uses `FOR UPDATE SKIP LOCKED`, so concurrent workers never take
the same task. SQLite (tests, local dev) ignores that clause.

Every state change is a conditional UPDATE that repeats its precondition (status, attempt
number, lease) in its WHERE clause and checks the row count, so the database re-checks at
write time. A check made in Python can't go stale between reading a task and writing it: a
stale worker, the reaper and a pause/block racing for the same row can't overwrite each other.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app import audit
from app.config import get_settings
from app.database import SessionLocal
from app.models import GateCode, Task, TaskStatus, utcnow
from app.gates import gate_is_approved
from app.task_registry import FAILURE_HOOKS, REQUIRED_GATES, ClaimedTask

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 1000


def backoff_seconds(attempt: int) -> int:
    """Delay before retrying after failed attempt number `attempt` (1-based): base * 2^(n-1), capped."""
    settings = get_settings()
    return min(settings.task_retry_base_seconds * 2 ** max(attempt - 1, 0), settings.task_retry_max_seconds)


def enqueue_task(
    db,
    project_id: uuid.UUID,
    task_type: str,
    payload: dict[str, Any] | None = None,
    actor: str = audit.SYSTEM_WORKER,
    idempotency_key: str | None = None,
) -> Task:
    """Add a task. If its type needs a gate that no person has approved yet, it starts `blocked`.

    Callers can't forget the gate: it comes from the type's registration. With an
    `idempotency_key`, asking again for the same key in the same project returns the existing
    task and adds nothing (a unique index backs this up). The caller commits.
    """
    if idempotency_key is not None:
        existing = db.scalar(select(Task).where(Task.project_id == project_id, Task.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    gate = REQUIRED_GATES.get(task_type)
    waiting = gate is not None and not gate_is_approved(db, project_id, gate)
    task = Task(
        project_id=project_id,
        type=task_type,
        payload=payload,
        status=TaskStatus.blocked if waiting else TaskStatus.queued,
        blocked_by_gate=gate if waiting else None,
        idempotency_key=idempotency_key,
    )
    try:
        # The lookup above is only an optimization: another request can insert the same
        # key before this one reaches the database. Keep that unique-index conflict inside
        # a savepoint so the caller's transaction stays usable.
        with db.begin_nested():
            db.add(task)
            db.flush()
    except IntegrityError:
        if idempotency_key is None:
            raise
        existing = db.scalar(select(Task).where(Task.project_id == project_id, Task.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    if waiting:
        audit.record(
            db, actor=actor, action="task.blocked", project_id=project_id,
            payload={"task_id": str(task.id), "type": task_type, "gate": gate.value},
        )
    return task


CLAIM_TRIES = 5


def _transition(db, task_id: uuid.UUID, *conditions, **values) -> bool:
    """Change one task only if `conditions` still hold when the database writes. True if it did."""
    result = db.execute(
        update(Task).where(Task.id == task_id, *conditions).values(**values).execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _owned(attempt: int) -> tuple:
    """WHERE conditions: the task is still running under this worker's attempt."""
    return (Task.status == TaskStatus.running, Task.attempts == attempt)


def claim_next_task(now: datetime | None = None, task_id: uuid.UUID | None = None) -> ClaimedTask | None:
    """Take the oldest due queued task, mark it running and give it a lease.

    With `task_id`, consider only that task (used by inline dev mode to run the task it just
    queued); it is claimed only if it is queued and due, exactly as for any worker.
    """
    now = now or utcnow()
    lease = now + timedelta(seconds=get_settings().task_lease_seconds)
    db = SessionLocal()
    try:
        for _ in range(CLAIM_TRIES):
            due = [Task.status == TaskStatus.queued, or_(Task.run_after.is_(None), Task.run_after <= now)]
            if task_id is not None:
                due.append(Task.id == task_id)
            task = db.scalar(
                select(Task)
                .where(*due)
                .order_by(Task.created_at.asc(), Task.id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return None
            attempt = task.attempts + 1
            # Another worker may have taken it since we read it (SKIP LOCKED isn't available everywhere).
            if _transition(
                db, task.id, Task.status == TaskStatus.queued, Task.attempts == task.attempts,
                status=TaskStatus.running, attempts=attempt, run_after=None, lease_expires_at=lease,
            ):
                claimed = ClaimedTask(task.id, task.project_id, task.type, task.payload, attempt)
                db.commit()
                return claimed
            db.rollback()
        return None
    except Exception:
        db.rollback()
        logger.exception("Unable to claim a task")
        return None
    finally:
        db.close()


def owns_task(task_id: uuid.UUID, attempt: int, db=None) -> bool:
    """Is the task still running under this attempt? With `db`, checks (and on PostgreSQL locks
    the row) inside the caller's transaction; otherwise uses a short session of its own."""
    query = select(Task.id).where(Task.id == task_id, *_owned(attempt)).with_for_update()
    if db is not None:
        return db.scalar(query) is not None
    with SessionLocal() as own:
        return own.scalar(query) is not None


def heartbeat(task_id: uuid.UUID, attempt: int, now: datetime | None = None) -> bool:
    """Extend the lease. False means this worker no longer owns the task and must stop."""
    now = now or utcnow()
    db = SessionLocal()
    try:
        renewed = _transition(
            db, task_id, *_owned(attempt), lease_expires_at=now + timedelta(seconds=get_settings().task_lease_seconds)
        )
        db.commit()
        return renewed
    finally:
        db.close()


def complete_task(task_id: uuid.UUID, attempt: int) -> bool:
    db = SessionLocal()
    try:
        done = _transition(db, task_id, *_owned(attempt), status=TaskStatus.completed, lease_expires_at=None, error=None)
        db.commit()
        return done
    finally:
        db.close()


def _run_failure_hook(info: ClaimedTask) -> None:
    """After a task has failed for good, let its type clean up the record it was working on."""
    hook = FAILURE_HOOKS.get(info.type)
    if hook is None:
        return
    try:
        hook(info)
    except Exception:
        logger.exception("Failure hook for task %s (%s) raised", info.id, info.type)


def _info(task: Task) -> ClaimedTask:
    return ClaimedTask(task.id, task.project_id, task.type, task.payload, task.attempts)


def _fail_or_retry(
    db, task: Task, error: str, now: datetime, actor: str, *conditions, permanent: bool = False
) -> TaskStatus | None:
    """Record `error`, then requeue with backoff, or fail the task if attempts are exhausted or retrying can't help.

    `task` is the row as read. The write only happens if it is still running under the same
    attempt and `conditions` still hold; otherwise nothing is written or audited and None is returned.
    """
    error = error[:MAX_ERROR_CHARS]
    if permanent or task.attempts >= task.max_attempts:
        status, run_after, action = TaskStatus.failed, None, "task.failed"
    else:
        status, run_after, action = TaskStatus.queued, now + timedelta(seconds=backoff_seconds(task.attempts)), "task.requeued"
    if not _transition(
        db, task.id, *_owned(task.attempts), *conditions,
        status=status, run_after=run_after, error=error, lease_expires_at=None,
    ):
        return None
    audit.record(
        db,
        actor=actor,
        action=action,
        project_id=task.project_id,
        payload={
            "task_id": str(task.id),
            "type": task.type,
            "attempt": task.attempts,
            "max_attempts": task.max_attempts,
            "error": error,
        },
    )
    return status


def fail_task(
    task_id: uuid.UUID, attempt: int, error: str, now: datetime | None = None, permanent: bool = False
) -> TaskStatus | None:
    """A worker reports failure. Returns the task's new status, or None if it no longer owns the task.

    `permanent` skips the retries (the failure can't be fixed by trying again)."""
    now = now or utcnow()
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.status != TaskStatus.running or task.attempts != attempt:
            return None
        status = _fail_or_retry(db, task, error, now, audit.SYSTEM_WORKER, permanent=permanent)
        info = _info(task)
        db.commit()
    finally:
        db.close()
    if status == TaskStatus.failed:
        _run_failure_hook(info)
    return status


def reap_expired_tasks(now: datetime | None = None) -> int:
    """Recover tasks whose worker stopped renewing the lease (crash, kill, hang)."""
    now = now or utcnow()
    db = SessionLocal()
    try:
        expired = db.scalars(
            select(Task)
            .where(Task.status == TaskStatus.running, Task.lease_expires_at < now)
            .with_for_update(skip_locked=True)
        ).all()
        reaped, gave_up = 0, []
        for task in expired:
            # Re-checks the lease at write time: a heartbeat may have renewed it since the read.
            outcome = _fail_or_retry(
                db, task, "The worker stopped responding (lease expired).", now, audit.SYSTEM_WORKER,
                Task.lease_expires_at < now,
            )
            if outcome is not None:
                reaped += 1
            if outcome == TaskStatus.failed:
                gave_up.append(_info(task))
        db.commit()
        for info in gave_up:
            _run_failure_hook(info)
        return reaped
    except Exception:
        db.rollback()
        logger.exception("Unable to reap expired tasks")
        return 0
    finally:
        db.close()


def block_claimed_task(task_id: uuid.UUID, attempt: int, gate: GateCode) -> bool:
    """Runtime backstop: a worker claimed a gated task whose gate isn't approved. Put it back, unrun.

    The attempt it never really made is not counted. Returns False if the task isn't this worker's.
    """
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or not _transition(
            db, task_id, *_owned(attempt),
            status=TaskStatus.blocked, blocked_by_gate=gate, attempts=max(attempt - 1, 0), lease_expires_at=None,
        ):
            return False
        audit.record(
            db, actor=audit.SYSTEM_WORKER, action="task.blocked", project_id=task.project_id,
            payload={"task_id": str(task.id), "type": task.type, "gate": gate.value, "via": "claim backstop"},
        )
        db.commit()
        return True
    finally:
        db.close()


def block_task(task_id: uuid.UUID, gate: GateCode) -> bool:
    """Hold a queued task until `gate` is approved by a person. Running tasks can't be blocked."""
    db = SessionLocal()
    try:
        held = _transition(db, task_id, Task.status == TaskStatus.queued, status=TaskStatus.blocked, blocked_by_gate=gate)
        db.commit()
        return held
    finally:
        db.close()


def pause_task(task_id: uuid.UUID) -> bool:
    """Take a queued task out of circulation. A running task is not interrupted."""
    db = SessionLocal()
    try:
        paused = _transition(db, task_id, Task.status == TaskStatus.queued, status=TaskStatus.paused)
        db.commit()
        return paused
    finally:
        db.close()


def resume_task(task_id: uuid.UUID) -> bool:
    db = SessionLocal()
    try:
        resumed = _transition(db, task_id, Task.status == TaskStatus.paused, status=TaskStatus.queued)
        db.commit()
        return resumed
    finally:
        db.close()
