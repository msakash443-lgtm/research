import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_queue as q
from app import task_registry as registry
from app import task_runner, worker
from gate_helpers import approve_earlier_gates
from app.database import SessionLocal
from app.gates import ensure_gates, release_tasks_for_approved_gates
from app.main import app
from app.models import AuditEvent, Gate, GateCode, GateStatus, Project, ProjectMember, ProjectRole, Task, TaskStatus


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "G"}).json()["id"]
    return client, user_id


@pytest.fixture
def gated_type():
    """A task type that must wait for gate G2, with a handler that records what ran."""
    ran = []
    registry.register("bulk_search", requires_gate=GateCode.G2)(lambda task: ran.append(task.id))
    yield ran
    registry.HANDLERS.pop("bulk_search", None)
    registry.REQUIRED_GATES.pop("bulk_search", None)


@pytest.fixture
def team():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"gt-owner-{tag}@example.com")
    project_id = owner.post("/api/projects", json={"title": "Gated tasks"}).json()["id"]
    sup, sup_id = _login(f"gt-sup-{tag}@example.com")
    co, co_id = _login(f"gt-co-{tag}@example.com")
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(sup_id), role=ProjectRole.supervisor))
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(co_id), role=ProjectRole.co_author))
        db.commit()
    return {"owner": owner, "supervisor": sup, "co_author": co, "ids": {"owner": owner_id, "supervisor": sup_id}, "project": project_id}


def _enqueue(project_id, type="bulk_search"):
    with SessionLocal() as db:
        task = q.enqueue_task(db, uuid.UUID(project_id), type, {"q": "x"})
        db.commit()
        return task.id


def _task(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        db.expunge(task)
        return task


def _approve(team, who, code="G2"):
    approve_earlier_gates(team["project"], code)  # gates go in order (M0.5.10)
    return team[who].post(f"/api/projects/{team['project']}/gates/{code}/approve", json={"note": "ok"})


def test_a_task_whose_gate_is_pending_starts_blocked_and_is_never_claimed(gated_type, team):
    task_id = _enqueue(team["project"])

    task = _task(task_id)
    assert task.status == TaskStatus.blocked and task.blocked_by_gate == GateCode.G2
    assert q.claim_next_task() is None
    assert task_runner.run_one_task() is False and gated_type == []


def test_a_task_type_without_a_gate_is_queued_normally(gated_type, team):
    registry.register("plain")(lambda task: None)
    try:
        task = _task(_enqueue(team["project"], "plain"))
    finally:
        registry.HANDLERS.pop("plain", None)

    assert task.status == TaskStatus.queued and task.blocked_by_gate is None


def test_approving_the_gate_releases_the_task_and_it_then_runs(gated_type, team):
    task_id = _enqueue(team["project"])

    assert _approve(team, "supervisor").status_code == 200

    released = _task(task_id)
    assert released.status == TaskStatus.queued and released.blocked_by_gate is None
    assert task_runner.run_one_task() is True
    assert gated_type == [task_id] and _task(task_id).status == TaskStatus.completed


def test_rejecting_the_gate_keeps_the_task_blocked(gated_type, team):
    task_id = _enqueue(team["project"])
    approve_earlier_gates(team["project"], "G2")

    rejected = team["owner"].post(f"/api/projects/{team['project']}/gates/G2/reject", json={"note": "search too narrow"})

    assert rejected.status_code == 200
    assert _task(task_id).status == TaskStatus.blocked
    assert task_runner.run_one_task() is False
    # ...and a later approval (after rework) still releases it.
    _approve(team, "owner")
    assert _task(task_id).status == TaskStatus.queued


def test_only_the_matching_gate_in_the_same_project_releases_a_task(gated_type, team):
    other_owner, _ = _login(f"gt-other-{uuid.uuid4().hex[:6]}@example.com")
    other_project = other_owner.post("/api/projects", json={"title": "Other"}).json()["id"]
    mine = _enqueue(team["project"])
    theirs = _enqueue(other_project)

    team["owner"].post(f"/api/projects/{team['project']}/gates/G1/approve")  # wrong gate
    assert _task(mine).status == TaskStatus.blocked
    _approve(team, "owner")  # right gate, my project

    assert _task(mine).status == TaskStatus.queued
    assert _task(theirs).status == TaskStatus.blocked


def test_a_person_who_may_not_decide_the_gate_releases_nothing(gated_type, team):
    task_id = _enqueue(team["project"])

    assert _approve(team, "co_author").status_code == 403

    assert _task(task_id).status == TaskStatus.blocked


def test_a_task_enqueued_after_the_gate_was_approved_is_queued_immediately(gated_type, team):
    _approve(team, "owner")

    assert _task(_enqueue(team["project"])).status == TaskStatus.queued


def test_release_and_blocking_are_audited_with_the_approving_person(gated_type, team):
    task_id = _enqueue(team["project"])
    _approve(team, "supervisor")

    with SessionLocal() as db:
        events = {e.action: e for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(team["project"])))}
    assert events["task.blocked"].payload_json["gate"] == "G2" and events["task.blocked"].payload_json["task_id"] == str(task_id)
    released = events["task.released"]
    assert released.actor == team["ids"]["supervisor"] and released.payload_json["released"] == 1


def test_backstop_a_gated_task_that_was_queued_by_mistake_is_re_blocked_not_run(gated_type, team):
    # Simulates a bug or bad data: a gated task inserted as queued while its gate is pending.
    with SessionLocal() as db:
        task = Task(project_id=uuid.UUID(team["project"]), type="bulk_search", status=TaskStatus.queued)
        db.add(task)
        db.commit()
        task_id = task.id

    assert task_runner.run_one_task() is True

    task = _task(task_id)
    assert gated_type == []  # the handler never ran
    assert task.status == TaskStatus.blocked and task.blocked_by_gate == GateCode.G2
    assert task.attempts == 0  # the attempt it never made isn't counted


def test_sweep_releases_only_tasks_whose_gate_a_person_already_approved(gated_type, team):
    waiting = _enqueue(team["project"])
    stuck = _enqueue(team["project"])
    with SessionLocal() as db:
        # Race: the approval landed but this task was blocked just after (so decide_gate missed it).
        gates = {g.code: g for g in ensure_gates(db, db.get(Project, uuid.UUID(team["project"])))}
        gates[GateCode.G2].status = GateStatus.approved
        db.commit()
        db.get(Task, waiting).blocked_by_gate = GateCode.G3  # still waiting on an unapproved gate
        db.flush()  # the session doesn't autoflush

        assert release_tasks_for_approved_gates(db) == 1
        db.commit()

    assert _task(stuck).status == TaskStatus.queued
    assert _task(waiting).status == TaskStatus.blocked


def test_the_sweep_never_approves_a_gate(gated_type, team):
    _enqueue(team["project"])

    assert worker.release_gated_tasks() == 0

    with SessionLocal() as db:
        statuses = {g.status for g in db.scalars(select(Gate).where(Gate.project_id == uuid.UUID(team["project"])))}
    assert statuses == {GateStatus.pending}


def test_the_worker_loop_releases_and_runs_tasks():
    import inspect

    source = inspect.getsource(worker.run_forever)
    assert "release_gated_tasks" in source and "run_one_task" in source


def test_only_the_gate_decision_route_may_release_tasks_for_a_gate():
    """release_tasks_for_gate is driven by a person's approval alone (static guard)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    callers = sorted(
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if re.search(r"(?<!def )release_tasks_for_gate\(", path.read_text(encoding="utf-8"))
    )
    assert callers == [str(Path("routers") / "gates.py")]
