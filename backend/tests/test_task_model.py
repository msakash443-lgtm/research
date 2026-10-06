import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import GateCode, Project, Task, TaskStatus, User


def _project(db):
    user = User(email=f"task-{uuid.uuid4().hex}@example.test")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title="Tasks")
    db.add(project)
    db.flush()
    return project


def test_a_new_task_has_safe_defaults():
    with SessionLocal() as db:
        project = _project(db)
        db.add(Task(project_id=project.id, type="research_run", payload={"run_id": "r1"}))
        db.commit()
        task = db.scalar(select(Task))

    assert task.status == TaskStatus.queued
    assert (task.attempts, task.max_attempts) == (0, 3)
    assert task.lease_expires_at is None and task.blocked_by_gate is None and task.error is None
    assert task.payload == {"run_id": "r1"}
    assert task.created_at is not None


def test_every_status_round_trips():
    with SessionLocal() as db:
        project = _project(db)
        for status in TaskStatus:
            gate = GateCode.G2 if status == TaskStatus.blocked else None
            db.add(Task(project_id=project.id, type=status.value, status=status, blocked_by_gate=gate))
        db.commit()
        stored = {t.type: t.status for t in db.scalars(select(Task))}

    assert stored == {s.value: s for s in TaskStatus}


def test_a_blocked_task_must_name_its_gate():
    with SessionLocal() as db:
        project = _project(db)
        db.add(Task(project_id=project.id, type="bulk_search", status=TaskStatus.blocked, blocked_by_gate=GateCode.G2))
        db.commit()
        db.add(Task(project_id=project.id, type="bulk_search", status=TaskStatus.blocked))
        with pytest.raises(IntegrityError):
            db.commit()


def test_max_attempts_must_be_at_least_one():
    with SessionLocal() as db:
        project = _project(db)
        db.add(Task(project_id=project.id, type="x", max_attempts=0))
        with pytest.raises(IntegrityError):
            db.commit()
