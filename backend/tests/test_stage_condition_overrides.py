import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import exit_conditions as ec
from app import stage_machine as sm
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project

REASON = "The research question is being drafted with the supervisor this week."


def _owner_at_idea_with_g1_approved():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "overrides@example.com", "display_name": "O"})
    pid = client.post("/api/projects", json={"title": "Overrides"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/gates/G1/approve", json={}).status_code == 200
    return client, pid


def _advance(client, pid, body=None):
    return client.post(f"/api/projects/{pid}/stage/advance", json=body or {})


def _stage(client, pid):
    return client.get(f"/api/projects/{pid}/stage").json()["stage"]


def test_an_unmet_recommended_condition_locks_the_move_until_overridden():
    client, pid = _owner_at_idea_with_g1_approved()
    locked = _advance(client, pid)
    assert locked.status_code == 409
    detail = locked.json()["detail"]
    assert "EC-1.2" in [c["id"] for c in detail["recommended"]]
    assert detail["required"] == []
    assert _stage(client, pid) == "idea"


def test_an_overridden_recommended_condition_moves_the_project_and_is_audited():
    client, pid = _owner_at_idea_with_g1_approved()
    moved = _advance(client, pid, {"override_reasons": {"EC-1.2": REASON}})
    assert moved.status_code == 200, moved.text
    assert _stage(client, pid) == "scoped"
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "stage.condition_overridden"
                )
            )
        )
    assert len(events) == 1
    assert events[0].payload_json["condition"] == "EC-1.2"
    assert events[0].payload_json["reason"] == REASON


def test_a_reason_for_a_condition_that_is_not_unmet_is_refused():
    client, pid = _owner_at_idea_with_g1_approved()
    response = _advance(client, pid, {"override_reasons": {"EC-0.2": REASON}})
    assert response.status_code == 422
    assert _stage(client, pid) == "idea"


def test_a_reason_shorter_than_ten_characters_is_refused():
    client, pid = _owner_at_idea_with_g1_approved()
    response = _advance(client, pid, {"override_reasons": {"EC-1.2": "short"}})
    assert response.status_code == 422
    assert _stage(client, pid) == "idea"


def test_a_required_condition_refuses_even_with_an_override_reason(monkeypatch):
    client, pid = _owner_at_idea_with_g1_approved()
    original = ec.conditions

    def with_a_required_row_unmet(db, project, target):
        # Forced state: no built Required row is reachable past the gate and stale checks in a small fixture.
        return [*original(db, project, target), ec.Condition("EC-9.9", ec.Level.required, False, "forced", None)]

    monkeypatch.setattr(ec, "conditions", with_a_required_row_unmet)
    response = _advance(client, pid, {"override_reasons": {"EC-1.2": REASON}})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert [c["id"] for c in detail["required"]] == ["EC-9.9"]
    assert _stage(client, pid) == "idea"


def test_the_ai_and_worker_get_the_same_recommended_lock():
    client, pid = _owner_at_idea_with_g1_approved()
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        with pytest.raises(sm.ConditionsUnmet) as blocked:
            sm.advance_stage(db, project, actor="agent:search")
        assert [c.id for c in blocked.value.recommended] == ["EC-1.2"]
        db.rollback()
    assert _stage(client, pid) == "idea"
