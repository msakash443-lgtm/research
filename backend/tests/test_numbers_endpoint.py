"""The Write step's number panel (M6.3.1): `GET /projects/{id}/documents/numbers` lists this project's analysis-run
outputs flattened to the dot-paths a `stat` node can name, and a number picked from that list actually saves as a
`stat` node `app.ledger.check_integrity` resolves (no `unresolved_number`)."""

from tests.api_helpers import create_project, insert_analysis_run, sign_in_user

OUTPUT = {"descriptives": {"age": {"n": 40, "mean": 35.5}}, "correlations": [{"r": 0.31}]}


def _section(client, pid, title, position):
    response = client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": position})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_numbers_endpoint_lists_every_real_number_flattened_to_a_path():
    client, _ = sign_in_user("num.panel.list@example.org")
    pid = create_project(client, "Number panel")
    run = insert_analysis_run(pid, OUTPUT)

    response = client.get(f"/api/projects/{pid}/documents/numbers")
    assert response.status_code == 200, response.text
    options = response.json()
    by_path = {o["path"]: o for o in options}
    assert by_path["descriptives.age.mean"] == {"analysis_run_id": run, "path": "descriptives.age.mean", "value": 35.5}
    assert by_path["correlations.0.r"]["value"] == 0.31
    assert "correlations.0" not in by_path  # a dict is not itself a number leaf


def test_numbers_endpoint_is_scoped_to_the_project():
    client, _ = sign_in_user("num.panel.scope@example.org")
    pid_a = create_project(client, "Number panel")
    pid_b = create_project(client, "Number panel")
    insert_analysis_run(pid_a, OUTPUT)

    response = client.get(f"/api/projects/{pid_b}/documents/numbers")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_a_number_from_the_panel_saves_as_a_stat_node_the_ledger_resolves():
    client, _ = sign_in_user("num.panel.insert@example.org")
    pid = create_project(client, "Number panel")
    did = _section(client, pid, "Results", 0)
    run = insert_analysis_run(pid, OUTPUT)

    options = client.get(f"/api/projects/{pid}/documents/numbers").json()
    chosen = next(o for o in options if o["path"] == "descriptives.age.mean")
    assert chosen["analysis_run_id"] == run

    content = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "stat", "attrs": {"analysis_run_id": chosen["analysis_run_id"], "path": chosen["path"]}},
    ]}]}
    response = client.post(
        f"/api/projects/{pid}/documents/{did}/versions",
        json={"base_number": 0, "client_save_id": "save-1", "content_json": content},
    )
    assert response.status_code == 201, response.text

    link_check = client.get(f"/api/projects/{pid}/documents/{did}/link-check").json()
    assert link_check["findings"] == []
