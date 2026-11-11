import uuid

from fastapi.testclient import TestClient

from app.main import app


def _owner_project():
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    client.post("/api/auth/development/login", json={"email": f"jc-{tag}@example.com", "display_name": "J"})
    project_id = client.post("/api/projects", json={"title": "Journals"}).json()["id"]
    return client, project_id


def _journal(**extra):
    return {"name": "Example Journal", "source_url": "https://example.org/j", "checked_at": "2026-10-24", **extra}


def test_a_manually_entered_journal_cannot_be_marked_official_predatory():
    client, project_id = _owner_project()
    assert client.post(f"/api/projects/{project_id}/journals", json=_journal(official_predatory=True)).status_code == 422
    created = client.post(f"/api/projects/{project_id}/journals", json=_journal())
    assert created.status_code == 201 and created.json()["official_predatory"] is False


def test_only_the_official_list_import_sets_the_predatory_flag():
    client, project_id = _owner_project()
    rows = client.post(f"/api/projects/{project_id}/journals/official-list", json=[_journal()])
    assert rows.status_code == 201 and rows.json()[0]["official_predatory"] is True
