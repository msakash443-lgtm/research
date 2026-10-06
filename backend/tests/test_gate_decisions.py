import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Gate, GateCode, Project, ProjectMember, ProjectRole, User


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "G"}).json()["id"]
    return client, user_id


@pytest.fixture
def team():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"gd-owner-{tag}@example.com")
    project_id = owner.post("/api/projects", json={"title": "Gated"}).json()["id"]
    clients = {"owner": owner}
    ids = {"owner": owner_id}
    for role in (ProjectRole.supervisor, ProjectRole.co_author, ProjectRole.reviewer):
        client, user_id = _login(f"gd-{role.value}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(user_id), role=role))
            db.commit()
        clients[role.value], ids[role.value] = client, user_id
    return clients, ids, project_id


def _url(project_id, code, verb):
    return f"/api/projects/{project_id}/gates/{code}/{verb}"


def test_gate_list_shows_all_eleven_pending_with_required_roles(team):
    clients, _, project_id = team

    gates = clients["supervisor"].get(f"/api/projects/{project_id}/gates").json()

    assert [g["code"] for g in gates] == [f"G{n}" for n in range(1, 12)]
    assert {g["status"] for g in gates} == {"pending"}
    by_code = {g["code"]: g for g in gates}
    assert by_code["G1"]["required_roles"] == ["owner", "supervisor"] and by_code["G1"]["can_decide"] is True
    assert by_code["G7"]["required_roles"] == ["owner"] and by_code["G7"]["can_decide"] is False  # supervisor
    reviewer_view = clients["reviewer"].get(f"/api/projects/{project_id}/gates").json()
    assert not any(g["can_decide"] for g in reviewer_view)


def test_supervisor_approves_a_normal_gate_and_it_is_recorded(team):
    clients, ids, project_id = team

    response = clients["supervisor"].post(_url(project_id, "G1", "approve"), json={"note": "Direction agreed"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved" and body["decided_by"] == ids["supervisor"] and body["note"] == "Direction agreed"
    assert body["decided_at"] is not None and body["can_decide"] is False
    with SessionLocal() as db:
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "gate.approved")).one()
    assert event.actor == ids["supervisor"] and event.payload_json == {"gate": "G1", "role": "supervisor", "note": "Direction agreed"}


@pytest.mark.parametrize("code", ["G7", "G8", "G11"])
def test_owner_only_gates_refuse_a_supervisor_but_accept_the_owner(team, code):
    clients, _, project_id = team

    assert clients["supervisor"].post(_url(project_id, code, "approve")).status_code == 403
    assert clients["owner"].post(_url(project_id, code, "approve")).status_code == 200


@pytest.mark.parametrize("role", ["co_author", "reviewer"])
def test_co_authors_and_reviewers_can_never_decide_any_gate(team, role):
    clients, _, project_id = team

    for code in GateCode:
        for verb in ("approve", "reject"):
            assert clients[role].post(_url(project_id, code.value, verb), json={"note": "x"}).status_code == 403
    gates = clients["owner"].get(f"/api/projects/{project_id}/gates").json()
    assert {g["status"] for g in gates} == {"pending"}


def test_rejection_needs_a_reason_and_can_be_followed_by_approval(team):
    clients, _, project_id = team
    url = lambda verb: _url(project_id, "G2", verb)  # noqa: E731

    assert clients["owner"].post(url("reject")).status_code == 422
    assert clients["owner"].post(url("reject"), json={"note": "   "}).status_code == 422
    rejected = clients["owner"].post(url("reject"), json={"note": "Search too narrow"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert rejected.json()["can_decide"] is True  # can be re-decided after rework

    assert clients["supervisor"].post(url("approve"), json={"note": "Broadened"}).json()["status"] == "approved"


def test_an_approved_gate_cannot_be_decided_again(team):
    clients, _, project_id = team
    clients["owner"].post(_url(project_id, "G3", "approve"))

    assert clients["owner"].post(_url(project_id, "G3", "approve")).status_code == 409
    assert clients["owner"].post(_url(project_id, "G3", "reject"), json={"note": "changed my mind"}).status_code == 409
    gate = clients["owner"].get(f"/api/projects/{project_id}/gates").json()[2]
    assert gate["status"] == "approved"


def test_unknown_gate_code_is_rejected(team):
    clients, _, project_id = team

    assert clients["owner"].post(_url(project_id, "G12", "approve")).status_code == 422


def test_gates_are_created_on_demand_for_projects_without_them():
    owner, owner_id = _login("gd-legacy@example.com")
    with SessionLocal() as db:
        project = Project(owner_id=uuid.UUID(owner_id), title="Direct insert")
        db.add(project)
        db.commit()
        project_id = str(project.id)
        assert db.scalars(select(Gate).where(Gate.project_id == project.id)).all() == []

    assert owner.post(_url(project_id, "G1", "approve")).status_code == 200
    gates = owner.get(f"/api/projects/{project_id}/gates").json()
    assert len(gates) == 11 and gates[0]["status"] == "approved"


def test_no_agent_or_worker_code_can_decide_a_gate():
    """Gates are human-only: the decision code must not be reachable from automated code."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    automated = [*(app_dir / "agent").glob("*.py"), app_dir / "worker.py"]
    assert automated
    offenders = [
        path.name
        for path in automated
        if re.search(r"decide_gate|routers\.gates|GateStatus\.(approved|rejected)", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_nothing_in_the_app_approves_a_gate_except_the_decision_function():
    """Only decide_gate may set a gate's status to a decided value (attribute, keyword or bulk UPDATE)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    setters = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if re.search(r"status\s*=\s*(GateStatus\.(approved|rejected)|outcome)|update\(\s*Gate\s*\)", path.read_text(encoding="utf-8"))
    ]
    assert setters == [str(Path("routers") / "gates.py")]



@pytest.fixture
def race_db(tmp_path):
    """Sessions on a file-backed SQLite database, each with its own connection.

    The shared test engine is in-memory with a StaticPool (one connection for everyone), so
    two sessions on it can't interleave like two real requests do.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    engine.dispose()


def _race_project(Session):
    """An owner and a supervisor on a fresh project, outside the HTTP layer."""
    with Session() as db:
        owner, supervisor = User(email="race-o@example.test"), User(email="race-s@example.test")
        db.add_all([owner, supervisor])
        db.flush()
        project = Project(owner_id=owner.id, title="Race")
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=supervisor.id, role=ProjectRole.supervisor))
        db.commit()
        return project.id, owner.id, supervisor.id


def test_a_concurrent_decision_cannot_overwrite_an_approval(race_db):
    from fastapi import HTTPException

    from app.gates import ensure_gates
    from app.models import GateStatus
    from app.routers.gates import decide_gate

    project_id, owner_id, supervisor_id = _race_project(race_db)
    first, second = race_db(), race_db()
    try:
        p1, p2 = first.get(Project, project_id), second.get(Project, project_id)
        ensure_gates(first, p1)
        first.commit()
        # Both requests have read G2 as pending before either writes. Keep the second read alive:
        # the session's identity map is weak, and a reloaded row would hide the race window.
        seen_by_second = next(g for g in ensure_gates(second, p2) if g.code == GateCode.G2)
        assert seen_by_second.status == GateStatus.pending

        decide_gate(first, p1, first.get(User, owner_id), GateCode.G2, GateStatus.approved, None)
        with pytest.raises(HTTPException) as refused:
            decide_gate(second, p2, second.get(User, supervisor_id), GateCode.G2, GateStatus.rejected, "too late")
        assert refused.value.status_code == 409
    finally:
        first.close()
        second.close()

    with race_db() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == project_id, Gate.code == GateCode.G2))
        actions = db.scalars(select(AuditEvent.action).where(AuditEvent.project_id == project_id)).all()
    assert gate.status == GateStatus.approved and gate.decided_by == str(owner_id) and gate.note is None
    assert actions == ["gate.approved"]  # the refused decision left no audit event


def test_concurrent_gate_creation_does_not_fail(race_db):
    from app.gates import ensure_gates

    project_id, _, _ = _race_project(race_db)
    first, second = race_db(), race_db()
    try:
        p1, p2 = first.get(Project, project_id), second.get(Project, project_id)
        ensure_gates(first, p1)
        first.commit()
        # `second` read "no gates" before `first` committed: replay that stale first read, so it
        # tries to insert all eleven and hits the unique constraint.
        real_scalars, calls = second.scalars, []

        def stale_first_read(*args, **kwargs):
            calls.append(1)
            return iter([]) if len(calls) == 1 else real_scalars(*args, **kwargs)

        second.scalars = stale_first_read
        gates = ensure_gates(second, p2)
        second.commit()
        assert len(calls) == 2  # stale read, then the re-read after the refused insert
    finally:
        first.close()
        second.close()
    assert [g.code for g in gates] == list(GateCode)
    with race_db() as db:
        assert len(db.scalars(select(Gate).where(Gate.project_id == project_id)).all()) == 11
