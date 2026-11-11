import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.api_helpers import create_project, sign_in


def _project_with_source(client):
    project_id = create_project(client, "Verify")
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": "Paper", "evidence_excerpt": "text"}).json()
    return project_id, source


def test_new_source_is_unverified_and_owner_can_verify():
    client = sign_in("v1@example.com")
    project_id, source = _project_with_source(client)
    assert source["metadata_verified"] is False

    response = client.post(f"/api/projects/{project_id}/sources/{source['id']}/verify")

    assert response.status_code == 200
    assert response.json()["metadata_verified"] is True
    assert response.json()["evidence_excerpt"] == "text"
    assert client.post(f"/api/projects/{project_id}/sources/{source['id']}/verify").status_code == 200


def test_verify_requires_authentication():
    client = sign_in("v2@example.com")
    project_id, source = _project_with_source(client)

    anonymous = TestClient(app)
    response = anonymous.post(f"/api/projects/{project_id}/sources/{source['id']}/verify")

    assert response.status_code == 401
    assert client.get(f"/api/projects/{project_id}/sources").json()[0]["metadata_verified"] is False


def test_other_users_cannot_verify_and_unknown_source_404s():
    owner = sign_in("v3@example.com")
    project_id, source = _project_with_source(owner)
    intruder = sign_in("v4@example.com")

    assert intruder.post(f"/api/projects/{project_id}/sources/{source['id']}/verify").status_code == 404
    assert owner.post(f"/api/projects/{project_id}/sources/{uuid.uuid4()}/verify").status_code == 404
    assert owner.get(f"/api/projects/{project_id}/sources").json()[0]["metadata_verified"] is False
