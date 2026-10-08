import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "N"}).status_code == 200
    return client


def test_capture_is_saved_with_author_time_and_kind():
    client = _login("capture1@example.com")

    response = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "Does remote work widen the wage gap?"})

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "typed"
    assert body["status"] == "inbox"
    assert body["body"] == "Does remote work widen the wage gap?"
    assert body["project_id"] is None
    assert body["revision"] == 1
    assert body["captured_at"]
    assert body["owner_id"]


def test_capture_requires_authentication():
    anonymous = TestClient(app)
    response = anonymous.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "x"})
    assert response.status_code == 401


def test_capture_is_audited():
    client = _login("capture2@example.com")
    note_id = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "An idea"}).json()["id"]

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "note.created")).all()
        assert any(event.payload_json.get("note_id") == note_id for event in events)


def test_reposting_the_same_client_id_returns_the_same_note_not_a_duplicate():
    client = _login("idem1@example.com")
    payload = {"client_id": "retry-1", "kind": "typed", "body": "Saved once"}

    first = client.post("/api/notes", json=payload)
    second = client.post("/api/notes", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/api/notes").json()) == 1



def test_losing_a_race_on_the_same_client_id_returns_the_winners_note(monkeypatch):
    # Two submits of one capture both pass the "already saved?" lookup; the second insert then hits
    # the unique (owner, client_id) index and must return the first note, not a 500.
    from sqlalchemy import false

    from app.routers import notes as notes_router

    client = _login("idem-race@example.com")
    payload = {"client_id": "race-1", "kind": "typed", "body": "Saved once"}
    first = client.post("/api/notes", json=payload)

    real_select, calls = notes_router.select, []

    def select_missing_first_lookup(*args):
        calls.append(args)
        query = real_select(*args)
        return query.where(false()) if len(calls) == 1 else query

    monkeypatch.setattr(notes_router, "select", select_missing_first_lookup)
    second = client.post("/api/notes", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    monkeypatch.undo()
    assert len(client.get("/api/notes").json()) == 1

def test_a_different_users_client_id_is_a_different_note():
    a, b = _login("idem2a@example.com"), _login("idem2b@example.com")
    payload = {"client_id": "shared-id", "kind": "typed", "body": "mine"}

    note_a = a.post("/api/notes", json=payload).json()
    note_b = b.post("/api/notes", json=payload).json()

    assert note_a["id"] != note_b["id"]


def test_typed_note_requires_body():
    client = _login("validate1@example.com")
    response = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "   "})
    assert response.status_code == 422


def test_clip_note_requires_quoted_text_or_link():
    client = _login("validate2@example.com")

    bare = client.post("/api/notes", json={"client_id": "c1", "kind": "clip"})
    assert bare.status_code == 422

    with_link = client.post("/api/notes", json={"client_id": "c2", "kind": "clip", "source_url": "https://example.com/article"})
    assert with_link.status_code == 201
    assert with_link.json()["body"] == ""

    with_quote = client.post("/api/notes", json={"client_id": "c3", "kind": "clip", "quoted_text": "a clipped sentence"})
    assert with_quote.status_code == 201


def test_quoted_text_is_kept_separate_from_body():
    client = _login("validate3@example.com")
    response = client.post(
        "/api/notes",
        json={"client_id": "c1", "kind": "clip", "body": "my own comment", "quoted_text": "their exact words", "source_url": "https://example.com"},
    )
    body = response.json()
    assert body["body"] == "my own comment"
    assert body["quoted_text"] == "their exact words"


def test_voice_highlight_and_photo_kinds_are_not_available_yet():
    client = _login("validate4@example.com")
    for kind in ("voice", "highlight", "photo"):
        response = client.post("/api/notes", json={"client_id": f"c-{kind}", "kind": kind, "body": "x"})
        assert response.status_code == 422


def test_list_is_newest_first_and_can_filter_by_status():
    client = _login("list1@example.com")
    first = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "first", "captured_at": "2026-01-01T00:00:00Z"}).json()
    second = client.post("/api/notes", json={"client_id": "c2", "kind": "typed", "body": "second", "captured_at": "2026-01-02T00:00:00Z"}).json()
    client.post(f"/api/notes/{first['id']}/archive")

    notes = client.get("/api/notes").json()
    assert [note["id"] for note in notes] == [second["id"], first["id"]]

    inbox_only = client.get("/api/notes", params={"status": "inbox"}).json()
    assert [note["id"] for note in inbox_only] == [second["id"]]


def test_a_user_only_sees_their_own_notes():
    owner = _login("priv1@example.com")
    intruder = _login("priv2@example.com")
    note_id = owner.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "private"}).json()["id"]

    assert intruder.get(f"/api/notes/{note_id}").status_code == 404
    assert intruder.get(f"/api/notes/{note_id}/revisions").status_code == 404
    assert intruder.patch(f"/api/notes/{note_id}", json={"base_revision": 1, "body": "tampered"}).status_code == 404
    assert intruder.post(f"/api/notes/{note_id}/archive").status_code == 404
    assert intruder.post(f"/api/notes/{note_id}/lock", json={"ai_locked": True}).status_code == 404
    assert intruder.get("/api/notes").json() == []
    assert owner.get(f"/api/notes/{uuid.uuid4()}").status_code == 404


def test_editing_writes_a_new_revision_and_keeps_the_old_one():
    client = _login("edit1@example.com")
    note_id = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "first draft"}).json()["id"]

    response = client.patch(f"/api/notes/{note_id}", json={"base_revision": 1, "body": "second draft"})

    assert response.status_code == 200
    assert response.json()["body"] == "second draft"
    assert response.json()["revision"] == 2
    revisions = client.get(f"/api/notes/{note_id}/revisions").json()
    assert [r["body"] for r in revisions] == ["first draft", "second draft"]
    assert revisions[0]["revision"] == 1 and revisions[1]["revision"] == 2


def test_editing_with_a_stale_revision_is_refused_and_shows_the_current_copy():
    client = _login("edit2@example.com")
    note_id = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "v1"}).json()["id"]
    client.patch(f"/api/notes/{note_id}", json={"base_revision": 1, "body": "v2"})

    response = client.patch(f"/api/notes/{note_id}", json={"base_revision": 1, "body": "stale edit"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "note_conflict"
    assert detail["current"]["body"] == "v2"
    assert detail["current"]["revision"] == 2
    # Nothing was written for the rejected edit.
    assert [r["body"] for r in client.get(f"/api/notes/{note_id}/revisions").json()] == ["v1", "v2"]


def test_archive_sets_status_and_is_audited():
    client = _login("archive1@example.com")
    note_id = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "done"}).json()["id"]

    response = client.post(f"/api/notes/{note_id}/archive")

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "note.archived")) is not None


def test_lock_toggles_ai_locked_and_is_audited():
    client = _login("lock1@example.com")
    note_id = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "sensitive"}).json()["id"]
    assert client.get(f"/api/notes/{note_id}").json()["ai_locked"] is False

    locked = client.post(f"/api/notes/{note_id}/lock", json={"ai_locked": True})
    assert locked.status_code == 200
    assert locked.json()["ai_locked"] is True

    unlocked = client.post(f"/api/notes/{note_id}/lock", json={"ai_locked": False})
    assert unlocked.json()["ai_locked"] is False
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "note.locked")) is not None


def test_capture_can_be_locked_from_the_start():
    client = _login("lock2@example.com")
    note = client.post("/api/notes", json={"client_id": "c1", "kind": "typed", "body": "secret", "ai_locked": True}).json()
    assert note["ai_locked"] is True
