import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent
from tests.test_manuscript_export import _login, _new_document


def test_structured_review_comment_is_project_scoped_and_audited():
    client, user_id = _login(f"review-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Review"}).json()["id"]
    doc = _new_document(pid, user_id, "Introduction", 1)
    created = client.post(f"/api/projects/{pid}/reviews", json={
        "document_id": doc, "persona": "methodologist", "severity": "major", "body": "Clarify the sampling frame."
    })
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "open"
    assert client.get(f"/api/projects/{pid}/reviews").json()[0]["persona"] == "methodologist"
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "review.comment_created"))


def test_review_comment_rejects_free_form_invalid_severity_and_cross_project_document():
    client, user_id = _login(f"review-invalid-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Review"}).json()["id"]
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    doc = _new_document(other, user_id, "Other", 1)
    assert client.post(f"/api/projects/{pid}/reviews", json={
        "document_id": doc, "persona": "methodologist", "severity": "unknown", "body": "x"
    }).status_code == 404
def test_review_comment_invalid_severity_returns_422_not_500():
    client, user_id = _login(f"review-invalid-severity-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Review"}).json()["id"]
    doc = _new_document(pid, user_id, "Methods", 1)
    response = client.post(f"/api/projects/{pid}/reviews", json={
        "document_id": doc, "persona": "methodologist", "severity": "unknown", "body": "A comment"
    })
    assert response.status_code == 422
def test_review_issue_response_and_redline_are_structured_and_project_scoped():
    client, user_id = _login(f"issue-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Issue"}).json()["id"]
    doc = _new_document(pid, user_id, "Methods", 1)
    created = client.post(f"/api/projects/{pid}/reviews/issues", json={
        "document_id": doc, "title": "Sampling", "severity": "major",
        "redline": {"from": "old wording", "to": "new wording"},
    })
    assert created.status_code == 201, created.text
    issue_id = created.json()["id"]
    updated = client.patch(f"/api/projects/{pid}/reviews/issues/{issue_id}", json={
        "status": "resolved", "response_text": "Addressed in section 2.",
    })
    assert updated.status_code == 200
    assert updated.json()["status"] == "resolved"
    assert updated.json()["redline_json"]["to"] == "new wording"
