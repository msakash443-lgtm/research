import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent


def test_audit_event_round_trips_with_ai_provenance_fields():
    project_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(
            AuditEvent(
                project_id=project_id,
                actor="agent:research-run",
                action="research_run.completed",
                payload_json={"run_id": "r1", "sources": 3},
                model_id="fake-model",
                prompt_version="evidence-synthesis@1",
            )
        )
        db.commit()
        event = db.scalar(select(AuditEvent).where(AuditEvent.project_id == project_id))

    assert event.actor == "agent:research-run"
    assert event.payload_json == {"run_id": "r1", "sources": 3}
    assert event.model_id == "fake-model"
    assert event.prompt_version == "evidence-synthesis@1"
    assert event.timestamp is not None


def test_history_survives_without_a_matching_project_or_user():
    # actor and project_id are plain values, not foreign keys, so deleting either never loses history.
    with SessionLocal() as db:
        db.add(AuditEvent(project_id=None, actor=str(uuid.uuid4()), action="user.signed_in"))
        db.commit()
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "user.signed_in")) is not None
