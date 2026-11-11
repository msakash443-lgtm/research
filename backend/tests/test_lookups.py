import uuid

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.lookups import live_source_or_404, project_document_or_404
from app.models import Document, Project, Source, User


@pytest.fixture
def two_projects():
    with SessionLocal() as db:
        user = User(email=f"lookups-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        db.flush()
        mine, other = Project(owner_id=user.id, title="Mine"), Project(owner_id=user.id, title="Other")
        db.add_all([mine, other])
        db.flush()
        yield db, mine, other


def _not_found(call, detail):
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 404 and caught.value.detail == detail


def test_document_lookup_returns_own_document_and_404s_otherwise(two_projects):
    db, mine, other = two_projects
    own = Document(project_id=mine.id, title="Draft", position=0, created_by="t")
    foreign = Document(project_id=other.id, title="Draft", position=0, created_by="t")
    db.add_all([own, foreign])
    db.flush()

    assert project_document_or_404(db, mine, own.id) is own
    _not_found(lambda: project_document_or_404(db, mine, foreign.id), "Document not found")
    _not_found(lambda: project_document_or_404(db, mine, uuid.uuid4()), "Document not found")


def test_source_lookup_skips_merged_and_foreign_sources(two_projects):
    db, mine, other = two_projects
    live = Source(project_id=mine.id, title="Kept")
    foreign = Source(project_id=other.id, title="Elsewhere")
    db.add_all([live, foreign])
    db.flush()
    merged = Source(project_id=mine.id, title="Merged away", merged_into=live.id)
    db.add(merged)
    db.flush()

    assert live_source_or_404(db, mine, live.id) is live
    _not_found(lambda: live_source_or_404(db, mine, merged.id), "Source not found")
    _not_found(lambda: live_source_or_404(db, mine, foreign.id), "Source not found")
    _not_found(lambda: live_source_or_404(db, mine, uuid.uuid4()), "Source not found")
