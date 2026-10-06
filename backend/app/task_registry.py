"""Registry mapping a task `type` to the code that runs it.

A handler takes the `ClaimedTask` and returns normally on success. Any exception fails
the attempt (and is retried with backoff until `max_attempts`). Raise `PermanentTaskError`
when retrying cannot help; the task then ends `failed` immediately. An optional
`on_failure` hook runs once when a task of that type ends `failed` for good, so the
domain record it was working on can be marked failed too (never left looking in progress).

A handler that writes results calls `task.ensure_owned()` before each step that has side
effects (and with its session just before its final commit). If the worker lost its lease
and the task was handed to another worker, that raises `TaskOwnershipLost`: the handler
must stop and write nothing, because the new owner is doing the work.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import GateCode


class TaskOwnershipLost(Exception):
    """This worker no longer owns the task (lease lapsed, task taken over). Stop without writing."""


@dataclass(frozen=True)
class ClaimedTask:
    id: uuid.UUID
    project_id: uuid.UUID
    type: str
    payload: dict[str, Any] | None
    attempt: int
    # Set by the heartbeat thread when a lease renewal is refused.
    lost: threading.Event = field(default_factory=threading.Event, compare=False, repr=False)

    def ensure_owned(self, db=None) -> None:
        """Raise `TaskOwnershipLost` unless this attempt still owns the task.

        With `db`, the check runs in that session's transaction and, on PostgreSQL, locks the
        task row until the caller commits, so ownership can't change between this check and
        the caller's commit.
        """
        from app.task_queue import owns_task  # task_queue imports this module

        if self.lost.is_set() or not owns_task(self.id, self.attempt, db):
            self.lost.set()
            raise TaskOwnershipLost(f"Task {self.id} attempt {self.attempt} is no longer owned by this worker")


class PermanentTaskError(Exception):
    """Fail the task now, without further retries."""


Handler = Callable[[ClaimedTask], None]
FailureHook = Callable[[ClaimedTask], None]

HANDLERS: dict[str, Handler] = {}
FAILURE_HOOKS: dict[str, FailureHook] = {}
# Task types that must not run until a person has approved the named gate (spec section 8).
REQUIRED_GATES: dict[str, GateCode] = {}


def register(
    task_type: str, on_failure: FailureHook | None = None, requires_gate: GateCode | None = None
) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        if task_type in HANDLERS:
            raise ValueError(f"Task type {task_type!r} already has a handler")
        HANDLERS[task_type] = handler
        if on_failure is not None:
            FAILURE_HOOKS[task_type] = on_failure
        if requires_gate is not None:
            REQUIRED_GATES[task_type] = requires_gate
        return handler

    return decorator


def get_handler(task_type: str) -> Handler | None:
    return HANDLERS.get(task_type)
