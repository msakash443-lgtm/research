import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import task_queue as q
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, GateCode, Project, Task, TaskStatus, User

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


def _project():
    with SessionLocal() as db:
        user = User(email=f"q-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Queue")
        db.add(project)
        db.commit()
        return project.id


def _task(project_id, type="demo", **fields):
    with SessionLocal() as db:
        task = Task(project_id=project_id, type=type, **fields)
        db.add(task)
        db.commit()
        return task.id


def _get(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def _events(project_id):
    with SessionLocal() as db:
        return [(e.action, e.actor, e.payload_json) for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id))]


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def test_claim_takes_the_oldest_queued_task_and_leases_it():
    p = _project()
    # Explicit creation times: SQLite timestamps are one-second resolution, so ties would be broken by random id.
    first = _task(p, "first", payload={"n": 1}, created_at=NOW - timedelta(minutes=10))
    _task(p, "second", created_at=NOW - timedelta(minutes=5))

    claimed = q.claim_next_task(NOW)

    assert claimed.id == first and claimed.type == "first" and claimed.payload == {"n": 1} and claimed.attempt == 1
    task = _get(first)
    assert task.status == TaskStatus.running and task.attempts == 1
    assert _utc(task.lease_expires_at) == NOW + timedelta(seconds=get_settings().task_lease_seconds)


def test_a_task_is_never_claimed_twice_and_an_empty_queue_returns_none():
    p = _project()
    only = _task(p)

    assert q.claim_next_task(NOW).id == only
    assert q.claim_next_task(NOW) is None


def test_blocked_paused_running_and_finished_tasks_are_not_claimable():
    p = _project()
    _task(p, status=TaskStatus.blocked, blocked_by_gate=GateCode.G2)
    _task(p, status=TaskStatus.paused)
    _task(p, status=TaskStatus.running)
    _task(p, status=TaskStatus.completed)
    _task(p, status=TaskStatus.failed)

    assert q.claim_next_task(NOW) is None


def test_a_task_waiting_out_its_backoff_is_not_claimable_until_due():
    p = _project()
    task_id = _task(p, run_after=NOW + timedelta(seconds=30))

    assert q.claim_next_task(NOW) is None
    assert q.claim_next_task(NOW + timedelta(seconds=29)) is None
    assert q.claim_next_task(NOW + timedelta(seconds=30)).id == task_id


def test_heartbeat_extends_the_lease_for_the_current_attempt_only():
    p = _project()
    task_id = _task(p)
    claimed = q.claim_next_task(NOW)
    later = NOW + timedelta(seconds=200)

    assert q.heartbeat(task_id, claimed.attempt, later) is True
    assert _utc(_get(task_id).lease_expires_at) == later + timedelta(seconds=get_settings().task_lease_seconds)
    assert q.heartbeat(task_id, claimed.attempt + 1, later) is False
    assert q.heartbeat(uuid.uuid4(), 1, later) is False


def test_complete_marks_done_and_clears_the_lease():
    p = _project()
    task_id = _task(p)
    claimed = q.claim_next_task(NOW)

    assert q.complete_task(task_id, claimed.attempt) is True

    task = _get(task_id)
    assert task.status == TaskStatus.completed and task.lease_expires_at is None
    assert q.complete_task(task_id, claimed.attempt) is False  # not running any more


def test_failure_requeues_with_exponential_capped_backoff_then_fails_loudly():
    settings = get_settings()
    p = _project()
    task_id = _task(p, max_attempts=4)
    delays = []
    for expected_attempt in (1, 2, 3):
        # Move far enough ahead that the backoff from the previous failure has elapsed.
        clock = NOW + timedelta(hours=expected_attempt)
        claimed = q.claim_next_task(clock)
        assert claimed.attempt == expected_attempt
        assert q.fail_task(task_id, claimed.attempt, f"boom {expected_attempt}", clock) == TaskStatus.queued
        task = _get(task_id)
        delays.append(int((_utc(task.run_after) - clock).total_seconds()))
        assert task.error == f"boom {expected_attempt}" and task.lease_expires_at is None

    base, cap = settings.task_retry_base_seconds, settings.task_retry_max_seconds
    assert delays == [min(base * 2**i, cap) for i in range(3)]
    assert q.backoff_seconds(50) == cap

    last = q.claim_next_task(NOW + timedelta(hours=10))
    assert q.fail_task(task_id, last.attempt, "final boom", NOW) == TaskStatus.failed
    task = _get(task_id)
    assert task.status == TaskStatus.failed and task.error == "final boom" and task.attempts == 4
    assert q.claim_next_task(NOW + timedelta(days=1)) is None

    actions = [e[0] for e in _events(p)]
    assert actions.count("task.requeued") == 3 and actions.count("task.failed") == 1
    assert all(e[1] == "agent:worker" for e in _events(p))


def test_long_errors_are_truncated():
    p = _project()
    task_id = _task(p)
    claimed = q.claim_next_task(NOW)

    q.fail_task(task_id, claimed.attempt, "x" * 5000, NOW)

    assert len(_get(task_id).error) == q.MAX_ERROR_CHARS


def test_reaper_requeues_an_expired_task_and_fails_one_out_of_attempts():
    settings = get_settings()
    p = _project()
    retry = _task(p, "retry", status=TaskStatus.running, attempts=1, lease_expires_at=NOW - timedelta(seconds=1))
    exhausted = _task(
        p, "exhausted", status=TaskStatus.running, attempts=3, max_attempts=3, lease_expires_at=NOW - timedelta(minutes=5)
    )
    alive = _task(p, "alive", status=TaskStatus.running, attempts=1, lease_expires_at=NOW + timedelta(minutes=5))

    assert q.reap_expired_tasks(NOW) == 2

    retried, dead = _get(retry), _get(exhausted)
    assert retried.status == TaskStatus.queued and "lease expired" in retried.error
    assert _utc(retried.run_after) == NOW + timedelta(seconds=settings.task_retry_base_seconds)
    assert dead.status == TaskStatus.failed and "lease expired" in dead.error
    assert _get(alive).status == TaskStatus.running


def test_a_stale_worker_cannot_touch_a_task_that_was_reclaimed():
    p = _project()
    task_id = _task(p)
    old = q.claim_next_task(NOW)
    q.reap_expired_tasks(NOW + timedelta(hours=1))  # lease lapsed; requeued
    new = q.claim_next_task(NOW + timedelta(hours=2))
    assert new.id == task_id and new.attempt == 2

    assert q.heartbeat(task_id, old.attempt) is False
    assert q.complete_task(task_id, old.attempt) is False
    assert q.fail_task(task_id, old.attempt, "late") is None
    assert _get(task_id).status == TaskStatus.running
    assert q.complete_task(task_id, new.attempt) is True


def test_block_pause_and_resume():
    p = _project()
    task_id = _task(p)

    assert q.block_task(task_id, GateCode.G2) is True
    task = _get(task_id)
    assert task.status == TaskStatus.blocked and task.blocked_by_gate == GateCode.G2
    assert q.claim_next_task(NOW) is None
    assert q.pause_task(task_id) is False  # only queued tasks can be paused

    other = _task(p, "other")
    assert q.pause_task(other) is True and q.claim_next_task(NOW) is None
    assert q.resume_task(other) is True and q.claim_next_task(NOW).id == other
    assert q.resume_task(other) is False


def test_running_tasks_cannot_be_blocked_or_paused():
    p = _project()
    task_id = _task(p)
    q.claim_next_task(NOW)

    assert q.block_task(task_id, GateCode.G1) is False
    assert q.pause_task(task_id) is False
    assert _get(task_id).status == TaskStatus.running


def test_nothing_in_the_queue_can_release_a_blocked_task():
    """Blocked tasks stay blocked until a human approves the gate (rule 21); the queue has no release path."""
    import inspect

    public = {name for name, fn in inspect.getmembers(q, inspect.isfunction) if not name.startswith("_") and fn.__module__ == q.__name__}
    assert public == {
        "backoff_seconds", "claim_next_task", "heartbeat", "complete_task", "fail_task",
        "reap_expired_tasks", "block_task", "pause_task", "resume_task",
        # M0.5.4: these only create a task or put one back to `blocked`; none can release a blocked task.
        "enqueue_task", "block_claimed_task",
        # M0.6.9: read-only ownership check (SELECT … FOR UPDATE); it writes nothing.
        "owns_task",
    }


def _race(monkeypatch, competitor):
    """Run `competitor` once, inside the gap between a function's decision and its database write.

    That gap is where another worker or the reaper can act; the conditional write must notice.
    """
    real = q._transition
    state = {"ran": False}

    def interleaved(db, task_id, *conditions, **values):
        if not state["ran"]:
            state["ran"] = True
            competitor()
        return real(db, task_id, *conditions, **values)

    monkeypatch.setattr(q, "_transition", interleaved)
    return state


def test_a_stale_worker_cannot_complete_a_task_reclaimed_mid_write(monkeypatch):
    project_id = _project()
    task_id = _task(project_id)
    first = q.claim_next_task(NOW)
    later = NOW + timedelta(seconds=get_settings().task_lease_seconds + 1)
    second = {}

    def reaped_and_reclaimed():
        assert q.reap_expired_tasks(later) == 1
        second["claim"] = q.claim_next_task(later + timedelta(days=1))

    state = _race(monkeypatch, reaped_and_reclaimed)
    assert q.complete_task(task_id, first.attempt) is False
    assert state["ran"] and second["claim"].attempt == 2
    task = _get(task_id)
    assert task.status == TaskStatus.running and task.attempts == 2  # still the second worker's


def test_pause_or_block_cannot_land_on_a_task_claimed_mid_write(monkeypatch):
    project_id = _project()
    for action in (lambda tid: q.pause_task(tid), lambda tid: q.block_task(tid, GateCode.G2)):
        task_id = _task(project_id)
        _race(monkeypatch, lambda: q.claim_next_task(NOW))
        assert action(task_id) is False
        task = _get(task_id)
        assert task.status == TaskStatus.running and task.blocked_by_gate is None
        monkeypatch.undo()
        assert q.complete_task(task_id, task.attempts) is True


def test_the_reaper_leaves_a_task_whose_lease_was_renewed_mid_write(monkeypatch):
    project_id = _project()
    task_id = _task(project_id)
    claim = q.claim_next_task(NOW)
    later = NOW + timedelta(seconds=get_settings().task_lease_seconds + 1)
    _race(monkeypatch, lambda: q.heartbeat(task_id, claim.attempt, now=later))

    assert q.reap_expired_tasks(later) == 0
    task = _get(task_id)
    assert task.status == TaskStatus.running and task.attempts == 1 and task.error is None
    assert _events(project_id) == []  # nothing requeued, nothing audited


def test_a_failure_report_and_the_reaper_cannot_both_count_an_attempt(monkeypatch):
    project_id = _project()
    task_id = _task(project_id)
    claim = q.claim_next_task(NOW)
    later = NOW + timedelta(seconds=get_settings().task_lease_seconds + 1)
    _race(monkeypatch, lambda: q.reap_expired_tasks(later))

    assert q.fail_task(task_id, claim.attempt, "boom", now=later) is None  # the reaper got there first
    task = _get(task_id)
    assert task.status == TaskStatus.queued and task.attempts == 1
    assert [action for action, _, _ in _events(project_id)] == ["task.requeued"]
