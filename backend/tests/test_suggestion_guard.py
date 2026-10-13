import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.database import SessionLocal
from app.models import AiSuggestion, AiSuggestionEvent, Project, User


def _event_id():
    with SessionLocal() as db:
        user = User(email="guard@example.com", display_name="G")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="P")
        db.add(project)
        db.flush()
        suggestion = AiSuggestion(
            project_id=project.id, target_kind="note", target_id="n1", content="AI text",
            model_id="m", prompt_version="v1", producing_agent="test",
        )
        db.add(suggestion)
        db.flush()
        event = AiSuggestionEvent(suggestion_id=suggestion.id, seq=1, kind="accepted", actor="user:g")
        db.add(event)
        db.commit()
        return event.id, suggestion.id


def test_orm_update_of_an_event_is_rejected():
    event_id, _ = _event_id()
    with SessionLocal() as db:
        event = db.get(AiSuggestionEvent, event_id)
        event.kind = "discarded"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(AiSuggestionEvent, event_id).kind == "accepted"


def test_orm_delete_of_an_event_is_rejected():
    event_id, _ = _event_id()
    with SessionLocal() as db:
        db.delete(db.get(AiSuggestionEvent, event_id))
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(AiSuggestionEvent, event_id) is not None


def test_raw_sql_update_and_delete_of_an_event_are_rejected_by_the_database():
    event_id, _ = _event_id()
    with SessionLocal() as db:
        for statement in ("UPDATE ai_suggestion_events SET kind = 'discarded' WHERE id = :id", "DELETE FROM ai_suggestion_events WHERE id = :id"):
            with pytest.raises(DatabaseError, match="append-only"):
                db.execute(text(statement), {"id": event_id.hex})
            db.rollback()
        assert db.get(AiSuggestionEvent, event_id).kind == "accepted"


def test_two_events_cannot_claim_the_same_position_in_one_suggestions_history():
    _, suggestion_id = _event_id()
    with SessionLocal() as db:
        db.add(AiSuggestionEvent(suggestion_id=suggestion_id, seq=1, kind="undone", actor="user:g"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert len(db.scalars(select(AiSuggestionEvent)).all()) == 1
