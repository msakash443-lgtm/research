import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.audit import record
from app.database import SessionLocal
from app.models import AuditEvent


def _event_id():
    with SessionLocal() as db:
        event = record(db, actor="tester", action="thing.happened", project_id=uuid.uuid4(), payload={"k": 1})
        db.commit()
        return event.id


def test_orm_update_is_rejected():
    event_id = _event_id()
    with SessionLocal() as db:
        event = db.get(AuditEvent, event_id)
        event.action = "tampered"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(AuditEvent, event_id).action == "thing.happened"


def test_orm_delete_is_rejected():
    event_id = _event_id()
    with SessionLocal() as db:
        db.delete(db.get(AuditEvent, event_id))
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(AuditEvent, event_id) is not None


def test_raw_sql_update_and_delete_are_rejected_by_triggers():
    event_id = _event_id()
    for statement in (
        "UPDATE audit_events SET actor = 'someone-else' WHERE id = :id",
        "UPDATE audit_events SET payload_json = NULL WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
        "DELETE FROM audit_events",
    ):
        with SessionLocal() as db:
            with pytest.raises(DatabaseError, match="append-only"):
                db.execute(text(statement), {"id": event_id.hex} if ":id" in statement else {})
                db.commit()
            db.rollback()

    with SessionLocal() as db:
        event = db.get(AuditEvent, event_id)
        assert event.actor == "tester" and event.payload_json == {"k": 1}


def test_bulk_orm_delete_bypasses_events_but_not_the_trigger():
    _event_id()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.query(AuditEvent).delete()
            db.commit()
        db.rollback()
        assert db.scalar(select(AuditEvent.id)) is not None


def test_appending_still_works():
    first, second = _event_id(), _event_id()

    with SessionLocal() as db:
        assert {e.id for e in db.scalars(select(AuditEvent))} == {first, second}
