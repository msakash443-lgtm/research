"""Task-queue behaviour on a real PostgreSQL database (plan X.1.1).

The rest of the suite runs on in-memory SQLite, which ignores `FOR UPDATE SKIP LOCKED` and shares one
connection between all sessions, so it can't show what production relies on: two workers on separate
connections racing for the same rows. These tests need `TEST_POSTGRES_URL` (CI sets it; see ci.yml)
and are skipped without it, e.g.

    TEST_POSTGRES_URL=postgresql+psycopg://user:pass@localhost:5432/dbname pytest -q backend/tests/test_task_queue_postgres.py

The database named there is wiped: use a throwaway one.
"""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import task_queue as q
from app.config import get_settings
from app.database import Base
from app.models import AuditEvent, Project, Task, TaskStatus, User

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="TEST_POSTGRES_URL not set — these tests need a real Postgres instance (see ci.yml)"
)

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def pg(monkeypatch):
    """A sessionmaker on a fresh schema in the Postgres database; the task queue uses it too."""
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True, pool_size=10)
    assert engine.dialect.name == "postgresql", "TEST_POSTGRES_URL must point at PostgreSQL"
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    # task_queue opens its own sessions through this module-level name (the only module in the path that does).
    monkeypatch.setattr(q, "SessionLocal", Session)
    yield Session
    Base.metadata.drop_all(engine)
    engine.dispose()


def _project(Session):
    with Session() as db:
        user = User(email=f"pg-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Postgres queue")
        db.add(project)
        db.commit()
        return project.id


def _tasks(Session, project_id, count=1, **fields):
    with Session() as db:
        tasks = [Task(project_id=project_id, type="demo", **fields) for _ in range(count)]
        db.add_all(tasks)
        db.commit()
        return [task.id for task in tasks]


def _get(Session, task_id):
    with Session() as db:
        return db.get(Task, task_id)


def _actions(Session, project_id):
    with Session() as db:
        return [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id))]


def test_two_workers_on_separate_connections_never_claim_the_same_task(pg):
    task_ids = _tasks(pg, _project(pg), count=50)
    claimed = {"a": [], "b": []}
    errors = []
    start = threading.Barrier(2, timeout=30)

    def worker(name):
        try:
            start.wait()  # both begin together, so their claims overlap
            while (task := q.claim_next_task(NOW)) is not None:
                claimed[name].append(task.id)
        except Exception as exc:  # surfaced below; a thread can't fail the test itself
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in claimed]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors and not any(thread.is_alive() for thread in threads)
    a, b = claimed["a"], claimed["b"]
    assert set(a).isdisjoint(b)  # no task was handed to both workers
    assert len(a) + len(b) == len(set(a) | set(b)) == 50  # and no worker got the same task twice
    assert set(a) | set(b) == set(task_ids)  # every task was claimed
    assert a and b  # both workers really took work at the same time
    with pg() as db:
        tasks = db.scalars(select(Task).where(Task.id.in_(task_ids))).all()
    assert all(task.status == TaskStatus.running and task.attempts == 1 for task in tasks)


def test_failures_requeue_with_capped_exponential_backoff_then_fail(pg):
    settings = get_settings()
    project_id = _project(pg)
    (task_id,) = _tasks(pg, project_id, max_attempts=4)

    delays = []
    for attempt in (1, 2, 3):
        clock = NOW + timedelta(hours=attempt)  # past the previous backoff
        claimed = q.claim_next_task(clock)
        assert claimed.id == task_id and claimed.attempt == attempt
        assert q.claim_next_task(clock) is None  # running: nobody else gets it
        assert q.fail_task(task_id, attempt, f"boom {attempt}", clock) == TaskStatus.queued
        task = _get(pg, task_id)
        assert task.error == f"boom {attempt}" and task.lease_expires_at is None
        assert q.claim_next_task(task.run_after - timedelta(seconds=1)) is None  # still backing off
        delays.append(int((task.run_after - clock).total_seconds()))

    base, cap = settings.task_retry_base_seconds, settings.task_retry_max_seconds
    assert delays == [min(base * 2**i, cap) for i in range(3)]

    last = q.claim_next_task(NOW + timedelta(hours=10))
    assert q.fail_task(task_id, last.attempt, "final boom", NOW + timedelta(hours=10)) == TaskStatus.failed
    task = _get(pg, task_id)
    assert task.status == TaskStatus.failed and task.attempts == 4 and task.error == "final boom"
    assert q.claim_next_task(NOW + timedelta(days=1)) is None
    actions = _actions(pg, project_id)
    assert actions.count("task.requeued") == 3 and actions.count("task.failed") == 1


def test_an_expired_lease_is_reaped_and_the_stale_worker_is_locked_out(pg):
    settings = get_settings()
    project_id = _project(pg)
    retry_id, alive_id = _tasks(pg, project_id, count=2)
    (exhausted_id,) = _tasks(
        pg, project_id, status=TaskStatus.running, attempts=3, max_attempts=3, lease_expires_at=NOW - timedelta(minutes=5)
    )
    stale = q.claim_next_task(NOW)
    alive = q.claim_next_task(NOW + timedelta(seconds=settings.task_lease_seconds // 2))
    assert {stale.id, alive.id} == {retry_id, alive_id}
    expiry = NOW + timedelta(seconds=settings.task_lease_seconds + 1)  # stale's lease is over, alive's is not

    assert q.reap_expired_tasks(expiry) == 2

    retried, dead = _get(pg, stale.id), _get(pg, exhausted_id)
    assert retried.status == TaskStatus.queued and "lease expired" in retried.error
    assert retried.run_after == expiry + timedelta(seconds=settings.task_retry_base_seconds)
    assert dead.status == TaskStatus.failed and "lease expired" in dead.error
    assert _get(pg, alive.id).status == TaskStatus.running

    # The worker that lost its lease can no longer touch the task, even after another worker takes it over.
    assert q.heartbeat(stale.id, stale.attempt, expiry) is False
    assert q.complete_task(stale.id, stale.attempt) is False
    new = q.claim_next_task(retried.run_after)
    assert new.id == stale.id and new.attempt == stale.attempt + 1
    assert q.owns_task(stale.id, stale.attempt) is False
    assert q.complete_task(stale.id, stale.attempt) is False
    assert q.complete_task(new.id, new.attempt) is True
