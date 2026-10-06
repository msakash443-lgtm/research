import csv
import io

from fastapi.testclient import TestClient

from app.main import app


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, user_id


def _project_with_activity():
    owner, owner_id = _login("api-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "=HYPERLINK(\"http://evil\")"}).json()["id"]
    owner.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"})
    owner.post(f"/api/projects/{project_id}/sources", json={"title": "S"})
    return owner, owner_id, project_id


def test_members_can_read_the_trail_and_filter_it():
    owner, owner_id, project_id = _project_with_activity()
    reviewer, _ = _login("api-reviewer@example.com")
    owner.post(f"/api/projects/{project_id}/members", json={"email": "api-reviewer@example.com", "role": "reviewer"})
    url = f"/api/projects/{project_id}/audit"

    everything = reviewer.get(url)
    assert everything.status_code == 200
    assert {e["action"] for e in everything.json()} == {"project.created", "context.added", "source.created", "member.invited"}
    assert [e["action"] for e in owner.get(url, params={"action": "source.created"}).json()] == ["source.created"]
    assert {e["actor"] for e in owner.get(url, params={"actor": owner_id}).json()} == {owner_id}
    assert len(owner.get(url, params={"limit": 2}).json()) == 2
    assert len(owner.get(url, params={"limit": 2, "offset": 3}).json()) == 1
    assert owner.get(url, params={"limit": 0}).status_code == 422
    assert owner.get(url, params={"limit": 5000}).status_code == 422


def test_non_members_cannot_read_or_export():
    _, _, project_id = _project_with_activity()
    stranger, _ = _login("api-stranger@example.com")

    assert stranger.get(f"/api/projects/{project_id}/audit").status_code == 404
    assert stranger.get(f"/api/projects/{project_id}/audit/export").status_code == 404
    assert TestClient(app).get(f"/api/projects/{project_id}/audit").status_code == 401


def test_only_this_projects_events_are_returned():
    owner, _, project_id = _project_with_activity()
    other_id = owner.post("/api/projects", json={"title": "Other"}).json()["id"]

    mine = owner.get(f"/api/projects/{project_id}/audit").json()
    theirs = owner.get(f"/api/projects/{other_id}/audit").json()

    assert len(mine) == 3 and [e["action"] for e in theirs] == ["project.created"]


def test_json_export_is_a_complete_download():
    owner, _, project_id = _project_with_activity()

    response = owner.get(f"/api/projects/{project_id}/audit/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert f"audit-{project_id}.json" in response.headers["content-disposition"]
    rows = response.json()
    assert len(rows) == 3 and {"id", "timestamp", "actor", "action", "payload_json"} <= set(rows[0])


def test_csv_export_neutralises_spreadsheet_formulas():
    owner, owner_id, project_id = _project_with_activity()

    response = owner.get(f"/api/projects/{project_id}/audit/export", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["timestamp", "actor", "action", "model_id", "prompt_version", "payload_json", "id"]
    assert len(rows) == 4
    created = next(r for r in rows[1:] if r[2] == "project.created")
    assert created[1] == owner_id
    # The malicious "=HYPERLINK" title sits inside the payload JSON, so no cell starts with a formula
    # character. Direct neutralisation of such cells is covered by test_csv_cell_helper_prefixes_formula_starts.
    assert not any(cell.startswith(("=", "+", "@")) for row in rows for cell in row)


def test_csv_cell_helper_prefixes_formula_starts():
    from app.routers.audit import _csv_cell

    assert _csv_cell("=1+1") == "'=1+1"
    assert _csv_cell("+cmd") == "'+cmd"
    assert _csv_cell("-2") == "'-2"
    assert _csv_cell("@x") == "'@x"
    assert _csv_cell("plain") == "plain"
    assert _csv_cell(None) == ""


def test_export_rejects_unknown_formats():
    owner, _, project_id = _project_with_activity()

    assert owner.get(f"/api/projects/{project_id}/audit/export", params={"format": "xml"}).status_code == 422
