import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, ExternalIdentity


def _login(email, name="Real Name"):
    client = TestClient(app)
    response = client.post("/api/auth/development/login", json={"email": email, "display_name": name})
    assert response.status_code == 200
    return client, uuid.UUID(response.json()["id"])


def _capture_note(client, body):
    payload = {"client_id": uuid.uuid4().hex, "kind": "typed", "body": body}
    response = client.post("/api/notes", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_export_requires_sign_in():
    assert TestClient(app).get("/api/auth/export-my-data").status_code == 401


def test_export_holds_only_the_signed_in_users_own_data():
    alice, alice_id = _login("export-alice@example.com", "Alice")
    bob, _ = _login("export-bob@example.com", "Bob")
    db = SessionLocal()
    db.add(ExternalIdentity(user_id=alice_id, provider="google", subject="alice-google-subject"))
    db.commit()
    db.close()
    project = alice.post("/api/projects", json={"title": "Alice review"}).json()
    _capture_note(alice, "alice private thought")
    _capture_note(bob, "bob private thought")
    bob.post("/api/projects", json={"title": "Bob review"})

    response = alice.get("/api/auth/export-my-data")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["profile"]["email"] == "export-alice@example.com"
    assert data["profile"]["display_name"] == "Alice"
    assert [i["provider"] for i in data["external_identities"]] == ["google"]
    assert [p["title"] for p in data["projects"]] == ["Alice review"]
    assert data["projects"][0]["id"] == project["id"] and data["projects"][0]["role"] == "owner"
    assert [n["body"] for n in data["notes"]] == ["alice private thought"]
    assert data["notes"][0]["revisions"][0]["body"] == "alice private thought"
    assert {e["actor"] for e in data["audit_events"]} == {str(alice_id)}
    text = response.text
    assert "bob private thought" not in text and "export-bob@example.com" not in text and "Bob review" not in text


def test_each_export_is_recorded_in_the_audit_log():
    client, user_id = _login("export-audited@example.com")

    assert client.get("/api/auth/export-my-data").status_code == 200

    db = SessionLocal()
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.actor == str(user_id), AuditEvent.action == "user.data_exported")
    ).all()
    db.close()
    assert len(events) == 1
