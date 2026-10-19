"""The Write step's number panel (M6.3.1): `GET /projects/{id}/documents/numbers` lists this project's analysis-run
outputs flattened to the dot-paths a `stat` node can name, and a number picked from that list actually saves as a
`stat` node `app.ledger.check_integrity` resolves (no `unresolved_number`)."""

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AnalysisPlan, AnalysisRun, DatasetVersion

OUTPUT = {"descriptives": {"age": {"n": 40, "mean": 35.5}}, "correlations": [{"r": 0.31}]}


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "N"}).json()["id"]
    return client, user_id


def _project(client):
    return client.post("/api/projects", json={"title": "Number panel"}).json()["id"]


def _section(client, pid, title, position):
    response = client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": position})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _run(pid, output, user_id="u1"):
    with SessionLocal() as db:
        dataset = DatasetVersion(
            project_id=uuid.UUID(pid), version_no=1, kind="raw", name="d.csv", content="a\n1\n", sha256="0" * 64,
            row_count=1, column_names=["a"], created_by=user_id,
        )
        db.add(dataset)
        db.flush()
        plan = AnalysisPlan(
            project_id=uuid.UUID(pid), dataset_id=dataset.id, spec_json={"tests": [], "variables": [], "seed": 0},
            plan_sha256="1" * 64, environment_sha256="2" * 64, created_by=user_id,
        )
        db.add(plan)
        db.flush()
        run = AnalysisRun(
            project_id=uuid.UUID(pid), analysis_plan_id=plan.id, dataset_id=dataset.id, plan_sha256=plan.plan_sha256,
            dataset_sha256=dataset.sha256, script_sha256="3" * 64, environment_sha256=plan.environment_sha256,
            image="python@sha256:" + "4" * 64, output_json=output, created_by=user_id,
        )
        db.add(run)
        db.commit()
        return str(run.id)


def test_numbers_endpoint_lists_every_real_number_flattened_to_a_path():
    client, _ = _login("num.panel.list@example.org")
    pid = _project(client)
    run = _run(pid, OUTPUT)

    response = client.get(f"/api/projects/{pid}/documents/numbers")
    assert response.status_code == 200, response.text
    options = response.json()
    by_path = {o["path"]: o for o in options}
    assert by_path["descriptives.age.mean"] == {"analysis_run_id": run, "path": "descriptives.age.mean", "value": 35.5}
    assert by_path["correlations.0.r"]["value"] == 0.31
    assert "correlations.0" not in by_path  # a dict is not itself a number leaf


def test_numbers_endpoint_is_scoped_to_the_project():
    client, _ = _login("num.panel.scope@example.org")
    pid_a = _project(client)
    pid_b = _project(client)
    _run(pid_a, OUTPUT)

    response = client.get(f"/api/projects/{pid_b}/documents/numbers")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_a_number_from_the_panel_saves_as_a_stat_node_the_ledger_resolves():
    client, _ = _login("num.panel.insert@example.org")
    pid = _project(client)
    did = _section(client, pid, "Results", 0)
    run = _run(pid, OUTPUT)

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
