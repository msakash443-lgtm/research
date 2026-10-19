import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, ExternalIdentity, Note, NoteRevision, ProjectMember, User


def _login(email):
    client = TestClient(app)
    response = client.post("/api/auth/development/login", json={"email": email, "display_name": "Real Name"})
    assert response.status_code == 200
    return client, uuid.UUID(response.json()["id"])


def _erase(client, email):
    return client.post("/api/auth/erase-account", json={"confirm_email": email})


def test_erasure_blanks_personal_data_but_keeps_the_user_row():
    client, user_id = _login("erase-me@example.com")
    db = SessionLocal()
    db.add(ExternalIdentity(user_id=user_id, provider="google", subject="google-subject-1"))
    db.commit()
    db.close()

    assert _erase(client, "erase-me@example.com").status_code == 204

    db = SessionLocal()
    user = db.get(User, user_id)
    assert user is not None and user.erased_at is not None
    assert user.email.endswith("@erased.invalid") and "erase-me" not in user.email
    assert user.display_name is None
    assert db.scalars(select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)).all() == []
    db.close()


def test_erased_account_is_signed_out_and_cannot_be_reused():
    client, _ = _login("gone@example.com")
    other_device, _ = _login("gone@example.com")

    _erase(client, "gone@example.com")

    assert client.get("/api/auth/me").status_code == 401
    assert other_device.get("/api/auth/me").status_code == 401
    # Signing in again with the same email is a brand-new account, not the erased one.
    _, new_id = _login("gone@example.com")
    db = SessionLocal()
    assert db.get(User, new_id).erased_at is None
    db.close()


def test_erasure_requires_the_account_email_as_confirmation():
    client, user_id = _login("careful@example.com")

    assert _erase(client, "someone-else@example.com").status_code == 400
    assert client.post("/api/auth/erase-account", json={}).status_code == 422

    db = SessionLocal()
    assert db.get(User, user_id).erased_at is None
    db.close()


def test_erasure_needs_a_signed_in_user():
    assert TestClient(app).post("/api/auth/erase-account", json={"confirm_email": "x@example.com"}).status_code == 401


def test_audit_trail_is_kept_and_records_the_erasure():
    client, user_id = _login("audited@example.com")
    project_id = client.post("/api/projects", json={"title": "Solo"}).json()["id"]

    assert _erase(client, "audited@example.com").status_code == 204

    db = SessionLocal()
    events = db.scalars(select(AuditEvent).where(AuditEvent.actor == str(user_id))).all()
    actions = {e.action for e in events}
    assert "user.erased" in actions and len(events) >= 2  # earlier history still there, still attributed
    erased = next(e for e in events if e.action == "user.erased")
    assert erased.payload_json == {"user_id": str(user_id)}
    assert "audited@example.com" not in str([e.payload_json for e in events])
    db.close()
    assert project_id  # the solo project still exists (deleting it is X.4.7.3)


def test_sole_owner_of_a_shared_project_must_hand_over_first():
    owner, owner_id = _login("sole-owner@example.com")
    _login("teammate@example.com")
    project_id = owner.post("/api/projects", json={"title": "Team"}).json()["id"]
    owner.post(f"/api/projects/{project_id}/members", json={"email": "teammate@example.com", "role": "co_author"})

    response = _erase(owner, "sole-owner@example.com")

    assert response.status_code == 409
    db = SessionLocal()
    assert db.get(User, owner_id).erased_at is None
    db.close()


def test_erasure_allowed_once_another_owner_exists_and_membership_is_kept():
    owner, owner_id = _login("first-owner@example.com")
    _, second_id = _login("second-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "Team"}).json()["id"]
    owner.post(f"/api/projects/{project_id}/members", json={"email": "second-owner@example.com", "role": "owner"})

    assert _erase(owner, "first-owner@example.com").status_code == 204

    db = SessionLocal()
    members = db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == uuid.UUID(project_id))).all()
    assert owner_id in members and second_id in members
    db.close()
    second, _ = _login("second-owner@example.com")
    listed = second.get(f"/api/projects/{project_id}/members").json()
    assert any(m["email"].endswith("@erased.invalid") for m in listed)


def test_erased_user_cannot_act_through_the_development_header():
    client, user_id = _login("header@example.com")
    _erase(client, "header@example.com")

    assert TestClient(app).get("/api/projects", headers={"X-User-Id": str(user_id)}).status_code == 401


def test_erasure_scrubs_private_unfiled_notes_but_leaves_filed_notes_alone():
    client, user_id = _login("note-taker@example.com")
    unfiled = client.post(
        "/api/notes", json={"client_id": "n1", "body": "my private thought", "quoted_text": "clipped text"}
    )
    assert unfiled.status_code == 201
    unfiled_id = uuid.UUID(unfiled.json()["id"])

    project = client.post("/api/projects", json={"title": "Filed notes stay", "description": "d"})
    project_id = project.json()["id"]
    filed = client.post(
        "/api/notes", json={"client_id": "n2", "body": "filed body", "quoted_text": None}
    )
    assert filed.status_code == 201
    filed_id = uuid.UUID(filed.json()["id"])
    db = SessionLocal()
    filed_row = db.get(Note, filed_id)
    filed_row.project_id = uuid.UUID(project_id)
    db.commit()
    db.close()

    response = _erase(client, "note-taker@example.com")
    assert response.status_code == 204

    db = SessionLocal()
    scrubbed = db.get(Note, unfiled_id)
    assert scrubbed.body == ""
    assert scrubbed.quoted_text is None
    revisions = db.scalars(select(NoteRevision).where(NoteRevision.note_id == unfiled_id)).all()
    assert any(r.body == "my private thought" for r in revisions)  # the original revision text is unreachable, not deleted
    assert revisions[-1].body == ""

    kept = db.get(Note, filed_id)
    assert kept.body == "filed body"

    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "user.unfiled_notes_scrubbed")).all()
    assert len(events) == 1
    assert events[0].payload_json["note_count"] == 1
    db.close()
