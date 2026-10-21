import uuid

from fastapi.testclient import TestClient  # noqa: F401  (used through the `run` fixture's client)

from tests.test_robustness_suite import CSV, run  # noqa: F401  (the `run` fixture: a locked plan with one stored run)


def _log_url(pid, plan_id):
    return f"/api/projects/{pid}/analysis-plans/{plan_id}/deviation-logs"


def _dropped_y_run(client, pid, plan_id, run_id):
    """A second, sandboxed run whose plan drops variable y (tests: descriptives only, variables: x)."""
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                    json={"kind": "alternative_spec", "drop_variable": "y"})
    assert r.status_code == 201, r.text
    return r.json()["compare_run_id"]


def test_a_deviation_with_no_reason_is_refused_and_nothing_is_stored(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    actual = _dropped_y_run(client, pid, plan_id, run_id)
    r = client.post(_log_url(pid, plan_id), json={"actual_run_id": actual, "reasons": {}})
    assert r.status_code == 422, r.text
    assert "test_removed:correlations" in r.text  # the first deviation with no reason is named
    assert client.get(_log_url(pid, plan_id)).json() == []


def test_reasons_are_stored_with_each_deviation_and_listed_back(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    actual = _dropped_y_run(client, pid, plan_id, run_id)
    reasons = {"variable_removed:y": "  Y is   a pilot item  ", "test_removed:correlations": "Only one variable left."}
    r = client.post(_log_url(pid, plan_id), json={"actual_run_id": actual, "reasons": reasons})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["planned_plan_id"] == plan_id
    assert body["actual_run_id"] == actual
    assert body["planned_dataset_sha256"] == body["actual_dataset_sha256"]
    assert [(d["key"], d["reason"]) for d in body["deviations"]] == [
        ("test_removed:correlations", "Only one variable left."),
        ("variable_removed:y", "Y is a pilot item"),
    ]
    listed = client.get(_log_url(pid, plan_id)).json()
    assert [row["id"] for row in listed] == [body["id"]]


def test_a_reason_that_names_no_deviation_is_refused(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    actual = _dropped_y_run(client, pid, plan_id, run_id)
    reasons = {"variable_removed:y": "fine", "variable_removed:typo": "fine"}
    r = client.post(_log_url(pid, plan_id), json={"actual_run_id": actual, "reasons": reasons})
    assert r.status_code == 422, r.text
    assert "variable_removed:typo" in r.text
    assert client.get(_log_url(pid, plan_id)).json() == []


def test_an_unchanged_rerun_logs_no_deviations(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    rerun = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                        json={"kind": "rerun_identity"}).json()["compare_run_id"]
    r = client.post(_log_url(pid, plan_id), json={"actual_run_id": rerun, "reasons": {}})
    assert r.status_code == 201, r.text
    assert r.json()["deviations"] == []


def test_the_pre_registered_plan_must_be_locked_first(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    actual = _dropped_y_run(client, pid, plan_id, run_id)
    unlocked = client.post(f"/api/projects/{pid}/datasets/{dataset_id}/analysis-plans",
                           json={"tests": ["descriptives"], "variables": ["x"], "seed": 1}).json()["id"]
    r = client.post(_log_url(pid, unlocked), json={"actual_run_id": actual, "reasons": {}})
    assert r.status_code == 409, r.text


def test_an_actual_run_from_no_such_project_is_a_404(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(_log_url(pid, plan_id), json={"actual_run_id": str(uuid.uuid4()), "reasons": {}})
    assert r.status_code == 404, r.text


def test_the_log_is_scoped_to_its_project(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    client.post("/api/auth/development/login", json={"email": f"o-{uuid.uuid4().hex}@example.com", "display_name": "O"})
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    assert client.get(_log_url(other, plan_id)).status_code == 404
