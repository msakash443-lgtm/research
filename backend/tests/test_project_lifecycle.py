"""Project archive and hard deletion (X.4.7.3, Q23: owner signs off alone, no DPO review)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Note, NoteAttachment, NoteKind, NoteStatus, Project, ProjectMember, ProjectRole, Source, utcnow
from app.object_storage import fulltext_key, get_object_store


@pytest.fixture(autouse=True)
def _scratch_object_store(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "U"}).status_code == 200
    return client


def _bare_project(owner_id: uuid.UUID, *, title: str = "Bare") -> uuid.UUID:
    """A project row with no API calls behind it, so it has no `audit_events` row at all
    (every project made through `POST /api/projects` records `project.created`)."""
    with SessionLocal() as db:
        project = Project(owner_id=owner_id, title=title)
        db.add(project)
        db.commit()
        return project.id


def test_owner_can_archive_and_unarchive():
    owner = _login("archiver@example.com")
    project_id = owner.post("/api/projects", json={"title": "Archivable"}).json()["id"]

    response = owner.post(f"/api/projects/{project_id}/archive")
    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] is not None

    # Hidden from the default list, still directly readable.
    assert project_id not in {p["id"] for p in owner.get("/api/projects").json()}
    assert project_id in {p["id"] for p in owner.get("/api/projects?include_archived=true").json()}
    assert owner.get(f"/api/projects/{project_id}").status_code == 200

    response = owner.post(f"/api/projects/{project_id}/unarchive")
    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] is None
    assert project_id in {p["id"] for p in owner.get("/api/projects").json()}


def test_archive_and_unarchive_are_owner_only():
    owner = _login("archive-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "Guarded"}).json()["id"]
    co_author = _login("archive-co-author@example.com")
    with SessionLocal() as db:
        co_author_id = co_author.get("/api/auth/me").json()["id"]
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(co_author_id), role=ProjectRole.co_author))
        db.commit()
    stranger = _login("archive-stranger@example.com")

    assert co_author.post(f"/api/projects/{project_id}/archive").status_code == 403
    assert stranger.post(f"/api/projects/{project_id}/archive").status_code == 404

    assert owner.post(f"/api/projects/{project_id}/archive").status_code == 200
    assert co_author.post(f"/api/projects/{project_id}/unarchive").status_code == 403
    assert stranger.post(f"/api/projects/{project_id}/unarchive").status_code == 404


def test_archiving_twice_or_unarchiving_an_active_project_is_refused():
    owner = _login("archive-twice@example.com")
    project_id = owner.post("/api/projects", json={"title": "Once"}).json()["id"]

    assert owner.post(f"/api/projects/{project_id}/unarchive").status_code == 409
    assert owner.post(f"/api/projects/{project_id}/archive").status_code == 200
    assert owner.post(f"/api/projects/{project_id}/archive").status_code == 409


def test_archived_project_refuses_writes_and_unarchive_restores_them():
    """Smoke: archive -> write refused -> unarchive -> write allowed."""
    owner = _login("archive-write-boundary@example.com")
    project_id = owner.post("/api/projects", json={"title": "Write boundary"}).json()["id"]

    assert owner.post(f"/api/projects/{project_id}/archive").status_code == 200

    blocked = owner.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"})
    assert blocked.status_code == 409, blocked.text
    assert "archived" in blocked.json()["detail"].lower()

    assert owner.post(f"/api/projects/{project_id}/unarchive").status_code == 200

    allowed = owner.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"})
    assert allowed.status_code == 201, allowed.text


def _archived_project_with_source(owner_client):
    owner_id = uuid.UUID(owner_client.get("/api/auth/me").json()["id"])
    project_id = uuid.UUID(owner_client.post("/api/projects", json={"title": "Archived notes"}).json()["id"])
    store = get_object_store(get_settings())
    with SessionLocal() as db:
        source = Source(project_id=project_id, title="Stored PDF")
        db.add(source)
        db.flush()
        source.fulltext_path = store.put(fulltext_key(project_id, source.id, "pdf"), b"%PDF-1.4 x").key
        db.commit()
        source_id = source.id
    assert owner_client.post(f"/api/projects/{project_id}/archive").status_code == 200
    return owner_id, project_id, source_id


def test_archived_project_refuses_note_highlight_filing_and_promotion():
    owner = _login("archive-notes@example.com")
    owner_id, project_id, source_id = _archived_project_with_source(owner)

    highlight = owner.post(
        "/api/notes/highlight",
        json={"client_id": "archived-hl", "source_id": str(source_id), "quoted_text": "x", "body": "c"},
    )
    assert highlight.status_code == 409, highlight.text
    assert "archived" in highlight.json()["detail"].lower()

    with SessionLocal() as db:
        note = Note(
            owner_id=owner_id, project_id=None, client_id="archived-file", kind=NoteKind.typed,
            body="private", status=NoteStatus.inbox, captured_at=utcnow(),
        )
        filed = Note(
            owner_id=owner_id, project_id=project_id, client_id="archived-promote", kind=NoteKind.typed,
            body="filed", status=NoteStatus.filed, captured_at=utcnow(),
        )
        db.add_all([note, filed])
        db.commit()
        note_id, filed_id = note.id, filed.id

    assert owner.post(f"/api/notes/{note_id}/file", json={"project_id": str(project_id)}).status_code == 409
    promote = owner.post(f"/api/notes/{filed_id}/promote", json={"kind": "memo", "content": "Why?"})
    assert promote.status_code == 409, promote.text

    assert owner.post(f"/api/projects/{project_id}/unarchive").status_code == 200
    assert owner.post(f"/api/notes/{note_id}/file", json={"project_id": str(project_id)}).status_code == 200


def test_archived_project_refuses_edit_archive_and_lock_of_its_filed_notes():
    owner = _login("archive-own-notes@example.com")
    owner_id = uuid.UUID(owner.get("/api/auth/me").json()["id"])
    project_id = uuid.UUID(owner.post("/api/projects", json={"title": "Archived own notes"}).json()["id"])
    with SessionLocal() as db:
        filed = Note(
            owner_id=owner_id, project_id=project_id, client_id="archived-own", kind=NoteKind.typed,
            body="filed", status=NoteStatus.filed, captured_at=utcnow(),
        )
        private = Note(
            owner_id=owner_id, project_id=None, client_id="archived-private", kind=NoteKind.typed,
            body="private", status=NoteStatus.inbox, captured_at=utcnow(),
        )
        db.add_all([filed, private])
        db.commit()
        filed_id, private_id = filed.id, private.id
    assert owner.post(f"/api/projects/{project_id}/archive").status_code == 200

    assert owner.patch(f"/api/notes/{filed_id}", json={"body": "new", "base_revision": 1}).status_code == 409
    assert owner.post(f"/api/notes/{filed_id}/archive").status_code == 409
    assert owner.post(f"/api/notes/{filed_id}/lock", json={"ai_locked": True}).status_code == 409
    # A private, unfiled note is not project content and stays editable.
    assert owner.post(f"/api/notes/{private_id}/lock", json={"ai_locked": True}).status_code == 200

    assert owner.post(f"/api/projects/{project_id}/unarchive").status_code == 200
    assert owner.post(f"/api/notes/{filed_id}/lock", json={"ai_locked": True}).status_code == 200


def test_archived_project_refuses_gate_decisions_section_approval_and_provenance_reports():
    owner = _login("archive-gates@example.com")
    project_id = owner.post("/api/projects", json={"title": "Archived gates"}).json()["id"]
    assert owner.post(f"/api/projects/{project_id}/archive").status_code == 200

    base = f"/api/projects/{project_id}"
    for path, body in [
        (f"{base}/gates/G1/approve", {}),
        (f"{base}/gates/G1/reject", {}),
        (f"{base}/gates/G1/reopen", {"reason": "changed my mind"}),
        (f"{base}/documents/{uuid.uuid4()}/approve", {}),
        (f"{base}/provenance-reports", {"scope": "manuscript"}),
    ]:
        response = owner.post(path, json=body)
        assert response.status_code == 409, (path, response.text)
        assert "archived" in response.json()["detail"].lower()

    assert owner.post(f"{base}/unarchive").status_code == 200
    after = owner.post(f"{base}/provenance-reports", json={"scope": "manuscript"})
    assert "archived" not in str(after.json().get("detail", "")).lower()


def test_delete_fails_loudly_and_keeps_the_project_when_object_storage_fails(monkeypatch):
    from app.object_storage import LocalDiskStore, ObjectStorageError

    owner_client = _login("delete-storage-fail@example.com")
    owner_id = uuid.UUID(owner_client.get("/api/auth/me").json()["id"])
    project_id = _bare_project(owner_id, title="Storage fails")
    store = get_object_store(get_settings())
    with SessionLocal() as db:
        source = Source(project_id=project_id, title="Stored")
        db.add(source)
        db.flush()
        source.fulltext_path = store.put(fulltext_key(project_id, source.id, "pdf"), b"%PDF-1.4 y").key
        db.commit()

    def broken_delete(self, key):
        raise ObjectStorageError("backend unavailable")

    monkeypatch.setattr(LocalDiskStore, "delete", broken_delete)
    response = owner_client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 502, response.text
    with SessionLocal() as db:
        assert db.get(Project, project_id) is not None
        assert db.query(Source).filter(Source.project_id == project_id).count() == 1


def test_delete_is_owner_only():
    owner = _login("delete-owner@example.com")
    project_id = str(_bare_project(uuid.UUID(owner.get("/api/auth/me").json()["id"])))
    co_author = _login("delete-co-author@example.com")
    with SessionLocal() as db:
        co_author_id = co_author.get("/api/auth/me").json()["id"]
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(co_author_id), role=ProjectRole.co_author))
        db.commit()
    stranger = _login("delete-stranger@example.com")

    assert co_author.delete(f"/api/projects/{project_id}").status_code == 403
    assert stranger.delete(f"/api/projects/{project_id}").status_code == 404
    assert owner.delete(f"/api/projects/{project_id}").status_code == 204


def test_delete_refuses_a_project_with_audit_rows():
    """Every project made through the API records `project.created`; deletion must refuse cleanly
    and name the blocking table instead of losing the audit trail or hitting a guard's own error."""
    owner = _login("delete-blocked@example.com")
    project_id = owner.post("/api/projects", json={"title": "Has history"}).json()["id"]

    response = owner.delete(f"/api/projects/{project_id}")
    assert response.status_code == 409, response.text
    assert "audit_events" in response.json()["detail"]

    # Refused cleanly: the project and its audit trail are untouched.
    assert owner.get(f"/api/projects/{project_id}").status_code == 200
    with SessionLocal() as db:
        assert db.get(Project, uuid.UUID(project_id)) is not None
        assert db.query(AuditEvent).filter(AuditEvent.project_id == uuid.UUID(project_id)).count() >= 1


def test_delete_removes_rows_and_stored_objects_for_a_project_with_no_audit_rows():
    owner_client = _login("delete-success@example.com")
    owner_id = uuid.UUID(owner_client.get("/api/auth/me").json()["id"])
    project_id = _bare_project(owner_id, title="No history")

    store = get_object_store(get_settings())
    fulltext_bytes = b"%PDF-1.4 matrix body"
    attachment_bytes = b"attachment bytes"
    with SessionLocal() as db:
        source = Source(project_id=project_id, title="Has a stored full text")
        db.add(source)
        db.flush()
        fulltext = store.put(fulltext_key(project_id, source.id, "pdf"), fulltext_bytes)
        source.fulltext_path = fulltext.key
        note = Note(
            owner_id=owner_id, project_id=project_id, client_id="lifecycle-test", kind=NoteKind.typed,
            body="A filed note with no revision history beyond creation", status=NoteStatus.filed, captured_at=utcnow(),
        )
        db.add(note)
        db.flush()
        stored_attachment = store.put(f"projects/{project_id}/notes/{note.id}/attachment.bin", attachment_bytes)
        db.add(NoteAttachment(note_id=note.id, path=stored_attachment.key, media_type="application/octet-stream",
                               size=len(attachment_bytes), sha256="0" * 64))
        db.commit()
        source_id, note_id, fulltext_key_value, attachment_key_value = source.id, note.id, fulltext.key, stored_attachment.key

    assert store.get(fulltext_key_value) == fulltext_bytes  # sanity: really stored before delete

    response = owner_client.delete(f"/api/projects/{str(project_id)}")
    assert response.status_code == 204, response.text

    with SessionLocal() as db:
        assert db.get(Project, project_id) is None
        assert db.get(Source, source_id) is None
        assert db.get(Note, note_id) is None

    from app.object_storage import ObjectNotFound
    with pytest.raises(ObjectNotFound):
        store.get(fulltext_key_value)
    with pytest.raises(ObjectNotFound):
        store.get(attachment_key_value)
