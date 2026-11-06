import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Project
from tests.test_manuscript_export import _login, _new_document, _save


def test_front_matter_request_requires_ai_and_a_saved_document():
    client, user_id = _login(f"front-matter-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Front matter"}).json()["id"]
    document_id = _new_document(pid, user_id, "Introduction", 1)
    response = client.post(f"/api/projects/{pid}/documents/{document_id}/front-matter", json={"kind": "abstract"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_saved_version"


def test_front_matter_ai_off_is_refused_before_queueing():
    client, user_id = _login(f"front-matter-off-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Front matter"}).json()["id"]
    document_id = _new_document(pid, user_id, "Introduction", 1)
    _save(client, pid, document_id, {"type": "doc", "content": []})
    with SessionLocal() as db:
        db.get(Project, uuid.UUID(pid)).ai_involvement = "off"
        db.commit()
    response = client.post(f"/api/projects/{pid}/documents/{document_id}/front-matter", json={"kind": "abstract"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_off"


def test_front_matter_route_is_project_scoped():
    client, user_id = _login(f"front-matter-isolation-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Front matter"}).json()["id"]
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    document_id = _new_document(other, user_id, "Other", 1)
    assert client.get(f"/api/projects/{pid}/documents/{document_id}/front-matter").status_code == 404

def test_front_matter_proposals_use_explicit_accept_edit_and_discard_events():
    from tests.test_suggestions_api import _owner, _suggest, _url

    client, project_id = _owner()
    accepted = _suggest(project_id, kind="front_matter", target_id="document-1")
    assert client.post(_url(project_id, f"/{accepted}/accept")).status_code == 200
    edited = _suggest(project_id, kind="front_matter", target_id="document-1")
    assert client.post(_url(project_id, f"/{edited}/edit-accept"), json={"text": "Researcher wording"}).status_code == 200
    discarded = _suggest(project_id, kind="front_matter", target_id="document-1")
    assert client.post(_url(project_id, f"/{discarded}/discard")).status_code == 200