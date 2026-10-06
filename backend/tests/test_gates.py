import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.gates import GATE_APPROVER_ROLES, required_roles
from app.main import app
from app.models import Gate, GateCode, GateStatus, Project, ProjectRole, User


def test_there_are_eleven_gates_g1_to_g11_in_order():
    assert [g.value for g in GateCode] == [f"G{n}" for n in range(1, 12)]


def test_every_gate_has_a_human_approver_policy():
    assert set(GATE_APPROVER_ROLES) == set(GateCode)
    for code in GateCode:
        roles = required_roles(code)
        assert ProjectRole.owner in roles  # an owner can always decide
        assert ProjectRole.reviewer not in roles and ProjectRole.co_author not in roles


def test_agreed_policy_owner_only_gates():
    owner_only = {c for c in GateCode if required_roles(c) == {ProjectRole.owner}}
    assert owner_only == {GateCode.G7, GateCode.G8, GateCode.G11}
    for code in set(GateCode) - owner_only:
        assert required_roles(code) == {ProjectRole.owner, ProjectRole.supervisor}


def test_new_project_gets_all_gates_pending_and_undecided():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "gate@example.com", "display_name": "G"})
    project_id = client.post("/api/projects", json={"title": "Gated"}).json()["id"]

    with SessionLocal() as db:
        gates = db.scalars(select(Gate).where(Gate.project_id == uuid.UUID(project_id))).all()

    assert {g.code for g in gates} == set(GateCode) and len(gates) == 11
    for gate in gates:
        assert gate.status == GateStatus.pending
        assert gate.decided_by is None and gate.decided_at is None and gate.note is None


def test_a_project_cannot_have_the_same_gate_twice():
    with SessionLocal() as db:
        user = User(email=f"gate-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Dup")
        db.add(project)
        db.flush()
        db.add(Gate(project_id=project.id, code=GateCode.G1))
        db.commit()
        db.add(Gate(project_id=project.id, code=GateCode.G1))
        with pytest.raises(IntegrityError):
            db.commit()
