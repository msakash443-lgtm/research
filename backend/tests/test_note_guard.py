import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.database import SessionLocal
from app.main import app
from app.models import NoteRevision


def _revision_id():
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": "guard@example.com", "display_name": "G"}).status_code == 200
    created = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "Original text."})
    assert created.status_code == 201
    with SessionLocal() as db:
        return db.scalar(select(NoteRevision.id))


def test_orm_update_of_a_revision_is_rejected():
    revision_id = _revision_id()
    with SessionLocal() as db:
        revision = db.get(NoteRevision, revision_id)
        revision.body = "Tampered."
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(NoteRevision, revision_id).body == "Original text."


def test_orm_delete_of_a_revision_is_rejected():
    revision_id = _revision_id()
    with SessionLocal() as db:
        revision = db.get(NoteRevision, revision_id)
        db.delete(revision)
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(NoteRevision, revision_id) is not None


def test_raw_sql_update_is_rejected_by_database_trigger():
    revision_id = _revision_id()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("UPDATE note_revisions SET body = 'Tampered.' WHERE id = :id"), {"id": revision_id.hex})
            db.commit()
        db.rollback()
        assert db.get(NoteRevision, revision_id).body == "Original text."


def test_raw_sql_delete_is_rejected_by_database_trigger():
    revision_id = _revision_id()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("DELETE FROM note_revisions WHERE id = :id"), {"id": revision_id.hex})
            db.commit()
        db.rollback()
        assert db.get(NoteRevision, revision_id) is not None
