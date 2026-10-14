"""Source excerpts API (X.31.10): list and add passages for a source, from the source panel."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "E"}).status_code == 200
    return client


def _project_and_source(client, title="Excerpt paper"):
    project_id = client.post("/api/projects", json={"title": "Excerpts"}).json()["id"]
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": title, "evidence_excerpt": "First quote.", "locator": "p. 1"})
    assert source.status_code == 201
    return project_id, source.json()["id"]


def test_list_returns_the_seeded_excerpt_with_its_locator_and_hash():
    client = _login("exc-list@example.com")
    project_id, source_id = _project_and_source(client)
    rows = client.get(f"/api/projects/{project_id}/sources/{source_id}/excerpts")
    assert rows.status_code == 200
    body = rows.json()
    assert [row["content"] for row in body] == ["First quote."]
    assert body[0]["locator"] == "p. 1"
    assert len(body[0]["content_hash"]) == 64


def test_add_saves_a_new_passage_and_records_an_audit_event():
    client = _login("exc-add@example.com")
    project_id, source_id = _project_and_source(client)
    created = client.post(
        f"/api/projects/{project_id}/sources/{source_id}/excerpts", json={"content": "  Second quote.  ", "locator": " p. 7 "}
    )
    assert created.status_code == 201
    assert created.json()["content"] == "Second quote."
    assert created.json()["locator"] == "p. 7"
    contents = [row["content"] for row in client.get(f"/api/projects/{project_id}/sources/{source_id}/excerpts").json()]
    assert sorted(contents) == ["First quote.", "Second quote."]
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "source.excerpt_added")).all()
        assert any((event.payload_json or {}).get("excerpt_id") == created.json()["id"] for event in events)


def test_blank_passage_is_refused_and_nothing_is_saved():
    client = _login("exc-blank@example.com")
    project_id, source_id = _project_and_source(client)
    assert client.post(f"/api/projects/{project_id}/sources/{source_id}/excerpts", json={"content": "   "}).status_code == 422
    assert client.post(f"/api/projects/{project_id}/sources/{source_id}/excerpts", json={"content": ""}).status_code == 422
    assert len(client.get(f"/api/projects/{project_id}/sources/{source_id}/excerpts").json()) == 1


def test_unknown_or_merged_source_is_not_found():
    client = _login("exc-missing@example.com")
    project_id, _ = _project_and_source(client)
    missing = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/api/projects/{project_id}/sources/{missing}/excerpts").status_code == 404
    assert client.post(f"/api/projects/{project_id}/sources/{missing}/excerpts", json={"content": "x"}).status_code == 404
