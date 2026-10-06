import uuid

from fastapi.testclient import TestClient

from app.main import app


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "V"}).status_code == 200
    return client


def _source(client):
    project_id = client.post("/api/projects", json={"title": "Verify"}).json()["id"]
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": "Paper", "evidence_excerpt": "text"}).json()
    return project_id, source


def test_new_source_is_unverified_and_owner_can_verify():
    client = _login("v1@example.com")
    project_id, source = _source(client)
    assert source["metadata_verified"] is False

    response = client.post(f"/api/projects/{project_id}/sources/{source['id']}/verify")

    assert response.status_code == 200
    assert response.json()["metadata_verified"] is True
    assert response.json()["evidence_excerpt"] == "text"
    assert client.post(f"/api/projects/{project_id}/sources/{source['id']}/verify").status_code == 200


def test_verify_requires_authentication():
    client = _login("v2@example.com")
    project_id, source = _source(client)

    anonymous = TestClient(app)
    response = anonymous.post(f"/api/projects/{project_id}/sources/{source['id']}/verify")

    assert response.status_code == 401
    assert client.get(f"/api/projects/{project_id}/sources").json()[0]["metadata_verified"] is False


def test_other_users_cannot_verify_and_unknown_source_404s():
    owner = _login("v3@example.com")
    project_id, source = _source(owner)
    intruder = _login("v4@example.com")

    assert intruder.post(f"/api/projects/{project_id}/sources/{source['id']}/verify").status_code == 404
    assert owner.post(f"/api/projects/{project_id}/sources/{uuid.uuid4()}/verify").status_code == 404
    assert owner.get(f"/api/projects/{project_id}/sources").json()[0]["metadata_verified"] is False
