import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.database import SessionLocal
from app.main import app
from app.models import SourceExcerpt


def _excerpt_id():
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": "imm@example.com", "display_name": "I"}).status_code == 200
    project_id = client.post("/api/projects", json={"title": "Immutable"}).json()["id"]
    created = client.post(
        f"/api/projects/{project_id}/sources", json={"title": "Paper", "evidence_excerpt": "Original quote.", "locator": "p. 1"}
    )
    assert created.status_code == 201
    with SessionLocal() as db:
        return db.scalar(select(SourceExcerpt.id))


def test_orm_update_of_content_is_rejected():
    excerpt_id = _excerpt_id()
    with SessionLocal() as db:
        excerpt = db.get(SourceExcerpt, excerpt_id)
        excerpt.content = "Tampered."
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()
        assert db.get(SourceExcerpt, excerpt_id).content == "Original quote."


def test_orm_update_of_hash_is_rejected():
    excerpt_id = _excerpt_id()
    with SessionLocal() as db:
        excerpt = db.get(SourceExcerpt, excerpt_id)
        excerpt.content_hash = hashlib.sha256(b"x").hexdigest()
        with pytest.raises(ValueError, match="immutable"):
            db.commit()


def test_raw_sql_update_is_rejected_by_database_trigger():
    excerpt_id = _excerpt_id()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text("UPDATE source_excerpts SET content = 'Tampered.' WHERE id = :id"), {"id": excerpt_id.hex})
            db.commit()
        db.rollback()
        assert db.get(SourceExcerpt, excerpt_id).content == "Original quote."


def test_locator_can_still_be_edited_and_hash_matches_content():
    excerpt_id = _excerpt_id()
    with SessionLocal() as db:
        excerpt = db.get(SourceExcerpt, excerpt_id)
        excerpt.locator = "p. 2"
        db.commit()
        db.refresh(excerpt)
        assert excerpt.locator == "p. 2"
        assert excerpt.content_hash == hashlib.sha256(excerpt.content.encode()).hexdigest()
