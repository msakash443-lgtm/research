import uuid

from fastapi.testclient import TestClient

from app.main import app


def _login(client: TestClient) -> None:
    client.post("/api/auth/development/login", json={"email": f"sec-{uuid.uuid4().hex}@example.com", "display_name": "S"})


def _project(client: TestClient) -> str:
    return client.post("/api/projects", json={"title": "Section binding"}).json()["id"]


def _section(client: TestClient, pid: str, title: str, position: int) -> str:
    response = client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": position})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _claim(client: TestClient, pid: str, text: str, section_id: str | None = None) -> dict:
    body: dict = {"text": text}
    if section_id is not None:
        body["section_id"] = section_id
    response = client.post(f"/api/projects/{pid}/claims", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_claim_created_in_a_section_of_its_project_records_that_section():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    claim = _claim(client, pid, "Remote teams share less knowledge", intro)
    assert claim["section_id"] == intro


def test_a_claim_cannot_be_bound_to_a_section_of_another_project():
    client = TestClient(app)
    _login(client)
    mine = _project(client)
    other = _project(client)
    foreign_section = _section(client, other, "Their introduction", 0)
    response = client.post(f"/api/projects/{mine}/claims", json={"text": "x", "section_id": foreign_section})
    assert response.status_code == 404


def test_the_outline_lists_only_the_claims_bound_to_that_section_with_their_verification_state():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    method = _section(client, pid, "Method", 1)
    first = _claim(client, pid, "Remote teams share less knowledge", intro)
    _claim(client, pid, "Sampling used a panel", method)
    _claim(client, pid, "Unplaced idea")
    outline = client.get(f"/api/projects/{pid}/documents/{intro}/outline")
    assert outline.status_code == 200, outline.text
    items = outline.json()
    assert [(item["claim_id"], item["text"], item["verified"]) for item in items] == [
        (first["id"], "Remote teams share less knowledge", False)
    ]


def test_the_outline_of_another_projects_section_is_not_found():
    client = TestClient(app)
    _login(client)
    mine = _project(client)
    other = _project(client)
    foreign_section = _section(client, other, "Their introduction", 0)
    assert client.get(f"/api/projects/{mine}/documents/{foreign_section}/outline").status_code == 404
