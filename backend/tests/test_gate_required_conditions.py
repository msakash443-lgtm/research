import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import exit_conditions as ec
from app.database import SessionLocal
from app.main import app
from app.models import Gate, GateCode, GateStatus


def _owner_project():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "gate-required-owner@example.com", "display_name": "O"})
    pid = client.post("/api/projects", json={"title": "Gate required conditions"}).json()["id"]
    return client, pid


def _gate_status(pid, code):
    with SessionLocal() as db:
        return db.scalar(select(Gate.status).where(Gate.project_id == uuid.UUID(pid), Gate.code == code))


def _approve_gates(client, pid, *codes):
    for code in codes:
        assert client.post(f"/api/projects/{pid}/gates/{code}/approve").status_code == 200


def _force_unmet_required(monkeypatch, condition_id):
    """Append a Required row that is unmet, on top of the real rows, so the refusal path can be reached."""
    real = ec.conditions

    def forced(db, project, target):
        found = list(real(db, project, target))
        found.append(ec.Condition(condition_id, ec.Level.required, False, f"{condition_id} forced unmet by this test", None))
        return found

    monkeypatch.setattr(ec, "conditions", forced)


def test_g3_approval_goes_through_when_no_other_required_condition_is_unmet():
    client, pid = _owner_project()
    _approve_gates(client, pid, "G1", "G2", "G3")
    assert _gate_status(pid, GateCode.G3) == GateStatus.approved


def test_g4_approval_goes_through_when_only_its_own_gate_row_is_unmet():
    client, pid = _owner_project()
    _approve_gates(client, pid, "G1", "G2", "G3", "G4")
    assert _gate_status(pid, GateCode.G4) == GateStatus.approved


def test_g3_approval_is_refused_and_lists_the_unmet_required_condition(monkeypatch):
    client, pid = _owner_project()
    _approve_gates(client, pid, "G1", "G2")
    _force_unmet_required(monkeypatch, "EC-4.2")
    response = client.post(f"/api/projects/{pid}/gates/G3/approve")
    assert response.status_code == 409
    assert [c["id"] for c in response.json()["detail"]["required"]] == ["EC-4.2"]
    assert _gate_status(pid, GateCode.G3) != GateStatus.approved


def test_g4_approval_is_refused_while_a_required_condition_for_its_stage_is_unmet(monkeypatch):
    client, pid = _owner_project()
    _approve_gates(client, pid, "G1", "G2", "G3")
    _force_unmet_required(monkeypatch, "EC-5.2")
    response = client.post(f"/api/projects/{pid}/gates/G4/approve")
    assert response.status_code == 409
    assert [c["id"] for c in response.json()["detail"]["required"]] == ["EC-5.2"]
    assert _gate_status(pid, GateCode.G4) != GateStatus.approved


def test_rejecting_g3_is_never_refused_by_required_conditions(monkeypatch):
    client, pid = _owner_project()
    _approve_gates(client, pid, "G1", "G2")
    _force_unmet_required(monkeypatch, "EC-4.2")
    response = client.post(f"/api/projects/{pid}/gates/G3/reject", json={"note": "Not ready to screen yet."})
    assert response.status_code == 200
