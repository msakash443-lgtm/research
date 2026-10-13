from fastapi.testclient import TestClient

from app.main import app


def _owner_project():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "conditions-api@example.com", "display_name": "C"})
    pid = client.post("/api/projects", json={"title": "Conditions API"}).json()["id"]
    return client, pid


def test_each_next_option_on_get_stage_carries_the_conditions_for_that_move():
    client, pid = _owner_project()
    options = client.get(f"/api/projects/{pid}/stage").json()["next"]
    assert options
    for option in options:
        endpoint = client.get(f"/api/projects/{pid}/stage/conditions", params={"target": option["stage"]}).json()
        assert option["conditions"] == endpoint


def test_a_gate_condition_is_unmet_with_a_fix_until_its_gate_is_approved():
    client, pid = _owner_project()
    rows = client.get(f"/api/projects/{pid}/stage/conditions", params={"target": "scoped"}).json()
    gate = next(c for c in rows if c["id"] == "EC-1.1")
    assert gate["met"] is False
    assert gate["fix"]
    approved = client.post(f"/api/projects/{pid}/gates/G1/approve", json={})
    assert approved.status_code == 200, approved.text
    rows = client.get(f"/api/projects/{pid}/stage/conditions", params={"target": "scoped"}).json()
    assert next(c for c in rows if c["id"] == "EC-1.1")["met"] is True


def test_an_unknown_target_stage_is_refused():
    client, pid = _owner_project()
    response = client.get(f"/api/projects/{pid}/stage/conditions", params={"target": "nowhere"})
    assert response.status_code == 422
