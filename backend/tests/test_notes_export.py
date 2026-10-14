"""Notes export (X.31.22): the caller's own notes as JSON or Markdown, every status, newest first."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "E"}).status_code == 200
    return client


def _capture(client, body, **extra):
    response = client.post("/api/notes", json={"client_id": f"c-{uuid.uuid4()}", "kind": "typed", "body": body, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_export_json_returns_own_notes_in_every_status_newest_first():
    client = _login("export-json-owner@example.com")
    first = _capture(client, "first thought")
    second = _capture(client, "second thought")
    client.post(f"/api/notes/{first}/archive")

    response = client.get("/api/notes/export", params={"format": "json"})

    assert response.status_code == 200
    notes = response.json()
    assert [note["body"] for note in notes] == ["second thought", "first thought"]
    assert {note["status"] for note in notes} == {"inbox", "archived"}
    assert notes[0]["id"] == second


def test_export_defaults_to_json():
    client = _login("export-default-owner@example.com")
    _capture(client, "a note")

    response = client.get("/api/notes/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()[0]["body"] == "a note"


def test_export_never_includes_another_users_notes():
    owner = _login("export-scope-owner@example.com")
    _capture(owner, "owner's private note")
    intruder = _login("export-scope-intruder@example.com")
    _capture(intruder, "intruder's note")

    bodies = [note["body"] for note in intruder.get("/api/notes/export", params={"format": "json"}).json()]
    markdown = intruder.get("/api/notes/export", params={"format": "md"}).text

    assert bodies == ["intruder's note"]
    assert "owner's private note" not in markdown


def test_export_markdown_keeps_quoted_text_separate_from_the_body():
    client = _login("export-md-owner@example.com")
    _capture(client, "my own words", kind="clip", quoted_text="a clipped sentence", source_url="https://example.com/a")

    response = client.get("/api/notes/export", params={"format": "md"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.text
    assert "my own words" in text
    assert "> a clipped sentence" in text
    assert "https://example.com/a" in text


def test_export_with_an_unknown_format_is_refused():
    client = _login("export-bad-format-owner@example.com")

    assert client.get("/api/notes/export", params={"format": "docx"}).status_code == 422


def test_export_is_not_mistaken_for_a_note_id():
    # Declared before GET /notes/{note_id}; if the order were wrong, "export" would fail as a UUID (422) instead.
    client = _login("export-routing-owner@example.com")

    response = client.get("/api/notes/export")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_export_is_audited_with_format_and_count():
    client = _login("export-audit-owner@example.com")
    _capture(client, "one")
    _capture(client, "two")

    assert client.get("/api/notes/export", params={"format": "md"}).status_code == 200

    with SessionLocal() as db:
        payloads = [e.payload_json for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "note.exported"))]
        assert {"format": "md", "count": 2} in payloads
