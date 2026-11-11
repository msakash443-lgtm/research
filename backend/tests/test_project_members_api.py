from tests.api_helpers import create_project, sign_in_user


def _setup():
    owner, owner_id = sign_in_user("mem-owner@example.com")
    project_id = create_project(owner, "Team")
    return owner, owner_id, project_id


def test_owner_invites_lists_and_removes_a_member():
    owner, owner_id, project_id = _setup()
    colleague, colleague_id = sign_in_user("mem-colleague@example.com")
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
    sup, _ = sign_in_user("mem-sup@example.com")
    co, co_id = sign_in_user("mem-co@example.com")
    sign_in_user("mem-target@example.com")
    owner.post(url, json={"email": "mem-sup@example.com", "role": "supervisor"})
    owner.post(url, json={"email": "mem-co@example.com", "role": "co_author"})

    for client in (sup, co):
        assert client.post(url, json={"email": "mem-target@example.com", "role": "reviewer"}).status_code == 403
        assert client.delete(f"{url}/{co_id}").status_code == 403
    stranger, _ = sign_in_user("mem-stranger@example.com")
    assert stranger.get(url).status_code == 404


def test_invite_edge_cases():
    owner, _, project_id = _setup()
    url = f"/api/projects/{project_id}/members"
    sign_in_user("mem-known@example.com")

    assert owner.post(url, json={"email": "nobody@example.com", "role": "reviewer"}).status_code == 404
    assert owner.post(url, json={"email": "mem-known@example.com", "role": "boss"}).status_code == 422
    assert owner.post(url, json={"email": "mem-known@example.com", "role": "reviewer"}).status_code == 201
    assert owner.post(url, json={"email": "mem-known@example.com", "role": "supervisor"}).status_code == 409
    assert owner.delete(f"{url}/00000000-0000-0000-0000-000000000000").status_code == 404


def test_last_owner_cannot_be_removed_but_a_second_owner_can_take_over():
    owner, owner_id, project_id = _setup()
    url = f"/api/projects/{project_id}/members"
    second, second_id = sign_in_user("mem-second@example.com")

    assert owner.delete(f"{url}/{owner_id}").status_code == 409
    assert owner.post(url, json={"email": "mem-second@example.com", "role": "owner"}).status_code == 201

    assert owner.delete(f"{url}/{owner_id}").status_code == 204
    # The creator is fully out: owner_id was reassigned, so the fallback doesn't keep them in.
    assert owner.get(f"/api/projects/{project_id}").status_code == 404
    assert second.get(f"/api/projects/{project_id}").status_code == 200
    assert second.post(f"/api/projects/{project_id}/sources", json={"title": "S"}).status_code == 201
