"""Plan M0.5.10 (Decisions log 2026-10-07): gates are decided in order, and an approval can be reopened
only before the project has reached the stage the gate completes, and only if no later gate is approved."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_queue as q
from app import task_registry as registry
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, GateCode, Project, ProjectMember, ProjectRole, ProjectStage as S, Task, TaskStatus

JOB = "reopen_test_job"


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "R"}).json()["id"]
    return client, user_id


@pytest.fixture
def team():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"ro-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Reopen"}).json()["id"]
    clients, ids = {"owner": owner}, {"owner": owner_id}
    for role in (ProjectRole.supervisor, ProjectRole.co_author, ProjectRole.reviewer):
        client, uid = _login(f"ro-{role.value}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(uid), role=role))
            db.commit()
        clients[role.value], ids[role.value] = client, uid
    return {"c": clients, "ids": ids, "pid": pid}


@pytest.fixture
def g2_job():
    """A task type that waits for G2."""
    registry.register(JOB, requires_gate=GateCode.G2)(lambda task: None)
    yield
    registry.HANDLERS.pop(JOB, None)
    registry.REQUIRED_GATES.pop(JOB, None)


def _post(t, who, code, verb, body=None):
    return t["c"][who].post(f"/api/projects/{t['pid']}/gates/{code}/{verb}", json=body)


def _approve(t, *codes, who="owner"):
    for code in codes:
        assert _post(t, who, code, "approve", {"note": "ok"}).status_code == 200, code


def _reopen(t, code, who="owner", reason="Scope changed after review"):
    return _post(t, who, code, "reopen", {"reason": reason} if reason is not None else {})


def _gates(t, who="owner"):
    return {g["code"]: g for g in t["c"][who].get(f"/api/projects/{t['pid']}/gates").json()}


def _set_stage(t, stage):
    with SessionLocal() as db:
        db.get(Project, uuid.UUID(t["pid"])).stage = stage
        db.commit()


def _actions(t):
    with SessionLocal() as db:
        return list(db.scalars(select(AuditEvent.action).where(AuditEvent.project_id == uuid.UUID(t["pid"]))))


# ---- ordering --------------------------------------------------------------------------------------

@pytest.mark.parametrize("verb,body", [("approve", None), ("reject", {"note": "not yet"})])
def test_a_gate_cannot_be_decided_before_the_earlier_ones_are_approved(team, verb, body):
    before = _actions(team)

    response = _post(team, "owner", "G3", verb, body)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["waiting_for"] == ["G1", "G2"] and "in order" in detail["message"]
    assert _gates(team)["G3"]["status"] == "pending"
    assert _actions(team) == before  # nothing recorded


def test_gates_can_be_approved_ahead_in_sequence_without_moving_the_project(team):
    _approve(team, "G1", "G2", "G3")

    gates = _gates(team)
    assert [gates[c]["status"] for c in ("G1", "G2", "G3", "G4")] == ["approved", "approved", "approved", "pending"]
    assert team["c"]["owner"].get(f"/api/projects/{team['pid']}").json()["stage"] == "idea"


def test_a_rejected_earlier_gate_holds_back_the_later_ones(team):
    assert _post(team, "supervisor", "G1", "reject", {"note": "Scope unclear"}).status_code == 200

    assert _post(team, "owner", "G2", "approve").status_code == 409
    _approve(team, "G1")
    _approve(team, "G2")


def test_the_gate_list_offers_only_the_next_decidable_gate(team):
    gates = _gates(team)
    assert [c for c, g in gates.items() if g["can_decide"]] == ["G1"]
    assert gates["G1"]["waiting_for"] == [] and gates["G3"]["waiting_for"] == ["G1", "G2"]

    _approve(team, "G1")
    assert [c for c, g in _gates(team).items() if g["can_decide"]] == ["G2"]


# ---- reopening ---------------------------------------------------------------------------------------

def test_an_approval_can_be_reopened_before_the_project_reaches_its_stage(team):
    _approve(team, "G1")
    _set_stage(team, S.scoped)
    assert _post(team, "supervisor", "G2", "approve", {"note": "Strategy fine"}).status_code == 200

    response = _reopen(team, "G2")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending" and body["decided_by"] is None and body["note"] is None and body["can_decide"] is True
    with SessionLocal() as db:
        event = db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(team["pid"]), AuditEvent.action == "gate.reopened")
        ).one()
    assert event.actor == team["ids"]["owner"]
    assert event.payload_json["reason"] == "Scope changed after review" and event.payload_json["gate"] == "G2"
    assert event.payload_json["previous"]["decided_by"] == team["ids"]["supervisor"]
    assert event.payload_json["previous"]["note"] == "Strategy fine" and event.payload_json["previous"]["decided_at"]


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reopening_needs_a_reason(team, reason):
    _approve(team, "G1")
    assert _reopen(team, "G1", reason=reason).status_code == 422
    assert _gates(team)["G1"]["status"] == "approved"


def test_only_an_approved_gate_can_be_reopened(team):
    assert _reopen(team, "G1").status_code == 409
    _post(team, "owner", "G1", "reject", {"note": "no"})
    assert _reopen(team, "G1").status_code == 409


@pytest.mark.parametrize("stage", [S.scoped, S.search_planned, S.retrieved])
def test_once_the_project_has_reached_the_stage_reopen_is_refused_in_favour_of_re_entry(team, stage):
    _approve(team, "G1")
    _set_stage(team, stage)  # G1 completes 'scoped'

    response = _reopen(team, "G1")

    assert response.status_code == 409 and "re-entry" in response.json()["detail"]
    assert _gates(team)["G1"]["status"] == "approved" and _gates(team)["G1"]["can_reopen"] is False


def test_a_later_approved_gate_must_be_reopened_first(team):
    _approve(team, "G1", "G2", "G3")

    blocked = _reopen(team, "G2")
    assert blocked.status_code == 409 and "G3" in blocked.json()["detail"]
    assert _gates(team)["G2"]["can_reopen"] is False and _gates(team)["G3"]["can_reopen"] is True

    assert _reopen(team, "G3").status_code == 200
    assert _reopen(team, "G2").status_code == 200
    assert {_gates(team)[c]["status"] for c in ("G2", "G3")} == {"pending"}
    assert _gates(team)["G1"]["status"] == "approved"  # no cascade backwards either


def test_after_a_reopen_the_later_gates_wait_again(team):
    _approve(team, "G1", "G2")
    _reopen(team, "G2")

    assert _post(team, "owner", "G3", "approve").status_code == 409


@pytest.mark.parametrize("who", ["co_author", "reviewer"])
def test_people_who_cannot_decide_a_gate_cannot_reopen_it(team, who):
    _approve(team, "G1")
    assert _reopen(team, "G1", who=who).status_code == 403
    assert _gates(team)["G1"]["status"] == "approved"


def test_an_owner_only_gate_can_only_be_reopened_by_an_owner(team):
    _approve(team, *[f"G{n}" for n in range(1, 8)])  # G7 is owner-only; the project stays at 'idea'

    assert _reopen(team, "G7", who="supervisor").status_code == 403
    assert _gates(team, who="supervisor")["G7"]["can_reopen"] is False
    assert _reopen(team, "G7").status_code == 200


def test_reopening_holds_queued_tasks_again_and_a_fresh_approval_releases_them(team, g2_job):
    _approve(team, "G1", "G2")
    with SessionLocal() as db:
        task = q.enqueue_task(db, uuid.UUID(team["pid"]), JOB, {"x": 1})
        db.commit()
        task_id = task.id
        assert task.status == TaskStatus.queued

    assert _reopen(team, "G2").status_code == 200

    with SessionLocal() as db:
        held = db.get(Task, task_id)
        assert (held.status, held.blocked_by_gate) == (TaskStatus.blocked, GateCode.G2)
    assert "task.blocked" in _actions(team)

    _approve(team, "G2")
    with SessionLocal() as db:
        assert db.get(Task, task_id).status == TaskStatus.queued


def test_reopening_leaves_tasks_of_other_gates_and_other_projects_alone(team, g2_job):
    other_owner, _ = _login(f"ro-other-{uuid.uuid4().hex[:6]}@example.com")
    other_pid = other_owner.post("/api/projects", json={"title": "Other"}).json()["id"]
    _approve(team, "G1", "G2")
    for code in ("G1", "G2"):
        assert other_owner.post(f"/api/projects/{other_pid}/gates/{code}/approve", json={}).status_code == 200
    with SessionLocal() as db:
        theirs = q.enqueue_task(db, uuid.UUID(other_pid), JOB, {"x": 2})
        ungated = q.enqueue_task(db, uuid.UUID(team["pid"]), "some_other_type", {"x": 3})
        db.commit()
        theirs_id, ungated_id = theirs.id, ungated.id

    _reopen(team, "G2")

    with SessionLocal() as db:
        assert db.get(Task, theirs_id).status == TaskStatus.queued
        assert db.get(Task, ungated_id).status == TaskStatus.queued


# ---- serialisation (M0.5.13) -----------------------------------------------------------------------

@pytest.mark.parametrize("verb, code, setup", [
    ("approve", "G2", ("G1",)),
    ("reject", "G2", ("G1",)),
    ("reopen", "G2", ("G1", "G2")),
])
def test_every_gate_change_locks_the_project_before_reading_the_gates(team, monkeypatch, verb, code, setup):
    """Decide and reopen both check *other* gates (ordering / later approvals), so each must hold the
    project row lock before that read; otherwise, on PostgreSQL, a reopen of G1 and an approval of G2
    can both commit and leave G2 approved behind a pending G1. SQLite ignores FOR UPDATE, so this
    checks the order of calls rather than racing two connections."""
    from sqlalchemy.orm import Session

    from app.routers import gates as gates_router

    _approve(team, *setup)
    events = []
    real_refresh, real_ensure = Session.refresh, gates_router.ensure_gates

    def refresh(self, instance, *args, **kwargs):
        if isinstance(instance, Project) and kwargs.get("with_for_update"):
            events.append("lock")
        return real_refresh(self, instance, *args, **kwargs)

    def ensure(db, project):
        events.append("read gates")
        return real_ensure(db, project)

    monkeypatch.setattr(Session, "refresh", refresh)
    monkeypatch.setattr(gates_router, "ensure_gates", ensure)
    body = {"reason": "Scope changed"} if verb == "reopen" else {"note": "Not yet"}
    assert _post(team, "owner", code, verb, body).status_code == 200
    assert events[:2] == ["lock", "read gates"]
