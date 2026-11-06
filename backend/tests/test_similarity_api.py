"""Similarity check API (X.31.14): a request queues a `similarity_check` task. The task reads a section's latest saved
version against the project's sources and stores one append-only row. Nothing returned exposes source text to a caller
who can't already read the source."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Document, DocumentVersion, SimilarityCheck, Source, SourceExcerpt, Task, excerpt_hash
from app.task_handlers import SIMILARITY_CHECK
from app.task_registry import get_handler

SOURCE_SENTENCE = "Unpaid care work limits how many hours women can spend in paid employment each week."


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "D"}).json()["id"]
    return client, user_id


def _project(client, title="Similarity"):
    return client.post("/api/projects", json={"title": title}).json()["id"]


def _document(project_id, owner_id, title="Discussion"):
    with SessionLocal() as db:
        document = Document(project_id=uuid.UUID(project_id), title=title, position=1, created_by=f"user:{owner_id}")
        db.add(document)
        db.commit()
        return str(document.id)


def _source_with_excerpt(project_id, title, content):
    """A project source with one excerpt, written straight to the database (excerpt routes come with X.31.10)."""
    with SessionLocal() as db:
        source = Source(project_id=uuid.UUID(project_id), title=title, origin="manual")
        db.add(source)
        db.flush()
        excerpt = SourceExcerpt(source_id=source.id, content=content, content_hash=excerpt_hash(content), created_by="test")
        db.add(excerpt)
        db.commit()
        return str(source.id), str(excerpt.id)


def _para(text, sid, by):
    attrs = {"sid": sid, "origin": "typed", "by": by, "sug": None, "note": None, "paste": None, "src": None}
    return {"type": "paragraph", "content": [{"type": "text", "text": text, "marks": [{"type": "prov", "attrs": attrs}]}]}


def _save(client, project_id, document_id, text, base, owner_id):
    content = {"type": "doc", "content": [_para(text, f"s-{uuid.uuid4().hex[:8]}", owner_id)]}
    return client.post(
        f"/api/projects/{project_id}/documents/{document_id}/versions",
        json={"base_number": base, "client_save_id": uuid.uuid4().hex, "content_json": content},
    )


def _request_check(client, project_id, document_id, **extra):
    return client.post(f"/api/projects/{project_id}/similarity-checks", json={"document_id": document_id, **extra})


def _run_task(payload):
    """Run a queued `similarity_check` task the way the worker would."""
    get_handler(SIMILARITY_CHECK)(SimpleNamespace(payload=payload, ensure_owned=lambda: None))


def _queued_payload(response):
    with SessionLocal() as db:
        return db.get(Task, uuid.UUID(response.json()["task_id"])).payload


def _check(client, project_id, document_id):
    """Queue a check, run its task, and return the newest check for the section."""
    response = _request_check(client, project_id, document_id)
    assert response.status_code == 202, response.text
    _run_task(_queued_payload(response))
    listed = client.get(f"/api/projects/{project_id}/similarity-checks", params={"document_id": document_id})
    assert listed.status_code == 200, listed.text
    return listed.json()[0]


def _version_ids(document_id):
    with SessionLocal() as db:
        versions = db.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(document_id)).order_by(DocumentVersion.number)
        ).all()
        return [str(v.id) for v in versions]


def test_a_verbatim_copy_of_a_project_source_is_flagged_with_the_source_and_excerpt():
    owner, owner_id = _login("sim-copy@example.com")
    project_id = _project(owner)
    source_id, excerpt_id = _source_with_excerpt(project_id, "Kumar 2021", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    assert _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id).status_code == 201

    body = _check(owner, project_id, document_id)

    assert body["status"] == "completed"
    assert body["summary"] == {"near_verbatim": 1, "close_paraphrase": 0, "quote_without_citation": 0}
    assert [(f["kind"], f["source_id"], f["excerpt_id"], f["source_title"]) for f in body["findings"]] == [
        ("near_verbatim", source_id, excerpt_id, "Kumar 2021"),
    ]
    assert "content" not in body["findings"][0]
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "similarity.completed", AuditEvent.project_id == uuid.UUID(project_id))) is not None
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "similarity.requested", AuditEvent.project_id == uuid.UUID(project_id))) is not None


def test_a_request_is_queued_and_writes_nothing_until_the_task_runs():
    owner, owner_id = _login("sim-queued@example.com")
    project_id = _project(owner)
    _source_with_excerpt(project_id, "Kumar 2021", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)

    response = _request_check(owner, project_id, document_id)

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    with SessionLocal() as db:
        task = db.get(Task, uuid.UUID(response.json()["task_id"]))
        assert task.type == SIMILARITY_CHECK
        assert db.scalar(select(SimilarityCheck).where(SimilarityCheck.document_id == uuid.UUID(document_id))) is None


def test_the_check_reads_the_version_pinned_when_it_was_requested():
    owner, owner_id = _login("sim-pinned@example.com")
    project_id = _project(owner)
    _source_with_excerpt(project_id, "Kumar 2021", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)
    response = _request_check(owner, project_id, document_id)
    requested_version = _version_ids(document_id)[-1]
    # A save made while the task waits must not change what the task checks.
    _save(owner, project_id, document_id, "Interviews with twenty nurses revealed shifting attitudes toward night shifts.", base=1, owner_id=owner_id)

    _run_task(_queued_payload(response))
    listed = owner.get(f"/api/projects/{project_id}/similarity-checks", params={"document_id": document_id}).json()

    assert listed[0]["version_id"] == requested_version
    assert listed[0]["summary"]["near_verbatim"] == 1


def test_a_check_uses_the_latest_saved_version_not_an_older_one():
    owner, owner_id = _login("sim-latest@example.com")
    project_id = _project(owner)
    _source_with_excerpt(project_id, "Kumar 2021", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)
    _save(owner, project_id, document_id, "Interviews with twenty nurses revealed shifting attitudes toward night shifts.", base=1, owner_id=owner_id)

    body = _check(owner, project_id, document_id)

    assert body["findings"] == []
    assert body["summary"]["near_verbatim"] == 0


def test_a_source_in_another_project_is_not_a_reference():
    owner, owner_id = _login("sim-isolation@example.com")
    project_id = _project(owner, "Here")
    other_project = _project(owner, "There")
    _source_with_excerpt(other_project, "Elsewhere 2020", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)

    assert _check(owner, project_id, document_id)["findings"] == []


def test_checks_are_listed_for_their_section_newest_first():
    owner, owner_id = _login("sim-list@example.com")
    project_id = _project(owner)
    _source_with_excerpt(project_id, "Kumar 2021", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)
    first = _check(owner, project_id, document_id)
    second = _check(owner, project_id, document_id)

    listed = owner.get(f"/api/projects/{project_id}/similarity-checks", params={"document_id": document_id})

    assert listed.status_code == 200, listed.text
    assert [c["id"] for c in listed.json()] == [second["id"], first["id"]]


def test_a_section_with_no_saved_version_cannot_be_checked():
    owner, owner_id = _login("sim-empty@example.com")
    project_id = _project(owner)
    document_id = _document(project_id, owner_id)

    response = _request_check(owner, project_id, document_id)

    assert response.status_code == 404
    assert "no saved versions" in response.json()["detail"]


def test_a_section_from_another_project_is_not_found():
    owner, owner_id = _login("sim-foreign@example.com")
    project_id = _project(owner, "Mine")
    other_project = _project(owner, "Theirs")
    foreign_document = _document(other_project, owner_id)
    _save(owner, other_project, foreign_document, SOURCE_SENTENCE, base=0, owner_id=owner_id)

    assert _request_check(owner, project_id, foreign_document).status_code == 404


def test_an_institutional_provider_reports_not_configured_and_writes_nothing():
    owner, owner_id = _login("sim-institution@example.com")
    project_id = _project(owner)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)

    response = _request_check(owner, project_id, document_id, provider="institutional:turnitin")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_configured"
    with SessionLocal() as db:
        assert db.scalar(select(SimilarityCheck).where(SimilarityCheck.document_id == uuid.UUID(document_id))) is None


def test_a_recorded_check_cannot_be_changed_or_deleted():
    owner, owner_id = _login("sim-append@example.com")
    project_id = _project(owner)
    _source_with_excerpt(project_id, "Kumar 2021", SOURCE_SENTENCE)
    document_id = _document(project_id, owner_id)
    _save(owner, project_id, document_id, SOURCE_SENTENCE, base=0, owner_id=owner_id)
    check_id = _check(owner, project_id, document_id)["id"]

    with SessionLocal() as db:
        row = db.get(SimilarityCheck, uuid.UUID(check_id))
        row.status = "failed"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
    with SessionLocal() as db:
        row = db.get(SimilarityCheck, uuid.UUID(check_id))
        with pytest.raises(ValueError, match="append-only"):
            db.delete(row)
            db.commit()
        db.rollback()
