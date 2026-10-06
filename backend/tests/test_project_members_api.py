from fastapi.testclient import TestClient

from app.main import app


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": email.split("@")[0]}).json()["id"]
    return client, user_id


def _setup():
    owner, owner_id = _login("mem-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "Team"}).json()["id"]
    return owner, owner_id, project_id


def test_owner_invites_lists_and_removes_a_member():
    owner, owner_id, project_id = _setup()
    colleague, colleague_id = _login("mem-colleague@example.com")
    url = f"/api/projects/{project_id}/members"

    invited = owner.post(url, json={"email": "Mem-Colleague@Example.com", "role": "co_author"})
    assert invited.status_code == 201
    assert invited.json()["user_id"] == colleague_id and invited.json()["role"] == "co_author"
    assert {m["role"] for m in owner.get(url).json()} == {"owner", "co_author"}
    assert colleague.get(url).status_code == 200  # members can see the team
    assert project_id in [p["id"] for p in colleague.get("/api/projects").json()]

    assert owner.delete(f"{url}/{colleague_id}").status_code == 204
    assert colleague.get(f"/api/projects/{project_id}").status_code == 404


def test_only_owners_manage_members():
    owner, _, project_id = _setup()
    url = f"/api/projects/{project_id}/members"
    sup, _ = _login("mem-sup@example.com")
    co, co_id = _login("mem-co@example.com")
    _login("mem-target@example.com")
    owner.post(url, json={"email": "mem-sup@example.com", "role": "supervisor"})
    owner.post(url, json={"email": "mem-co@example.com", "role": "co_author"})

    for client in (sup, co):
        assert client.post(url, json={"email": "mem-target@example.com", "role": "reviewer"}).status_code == 403
        assert client.delete(f"{url}/{co_id}").status_code == 403
    stranger, _ = _login("mem-stranger@example.com")
    assert stranger.get(url).status_code == 404


def test_invite_edge_cases():
    owner, _, project_id = _setup()
    url = f"/api/projects/{project_id}/members"
    _login("mem-known@example.com")

    assert owner.post(url, json={"email": "nobody@example.com", "role": "reviewer"}).status_code == 404
    assert owner.post(url, json={"email": "mem-known@example.com", "role": "boss"}).status_code == 422
    assert owner.post(url, json={"email": "mem-known@example.com", "role": "reviewer"}).status_code == 201
    assert owner.post(url, json={"email": "mem-known@example.com", "role": "supervisor"}).status_code == 409
    assert owner.delete(f"{url}/00000000-0000-0000-0000-000000000000").status_code == 404


def test_last_owner_cannot_be_removed_but_a_second_owner_can_take_over():
    owner, owner_id, project_id = _setup()
    url = f"/api/projects/{project_id}/members"
    second, second_id = _login("mem-second@example.com")

    assert owner.delete(f"{url}/{owner_id}").status_code == 409
    assert owner.post(url, json={"email": "mem-second@example.com", "role": "owner"}).status_code == 201

    assert owner.delete(f"{url}/{owner_id}").status_code == 204
    # The creator is fully out: owner_id was reassigned, so the fallback doesn't keep them in.
    assert owner.get(f"/api/projects/{project_id}").status_code == 404
    assert second.get(f"/api/projects/{project_id}").status_code == 200
    assert second.post(f"/api/projects/{project_id}/sources", json={"title": "S"}).status_code == 201
