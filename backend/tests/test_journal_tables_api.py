import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AnalysisRun
from tests.test_robustness_suite import CSV, run  # noqa: F401  (the `run` fixture: a locked plan with one stored run)


def _url(pid, plan_id, run_id):
    return f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/tables"


def test_tables_come_from_the_stored_run_output(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    r = client.get(_url(pid, plan_id, run_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tables"]["descriptives"]["rows"][0][:3] == ["x", "4", "2.50"]
    assert body["tables"]["correlations"]["columns"] == ["Variable", "x", "y"]
    assert body["output_sha256"]


def test_tables_are_scoped_to_their_project(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    client.post("/api/auth/development/login", json={"email": f"o-{uuid.uuid4().hex}@example.com", "display_name": "O"})
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    assert client.get(_url(other, plan_id, run_id)).status_code == 404


def test_unusable_output_is_a_409_not_a_500(run):  # noqa: F811
    client, pid, dataset_id, plan_id, run_id = run
    with SessionLocal() as db:
        row = db.get(AnalysisRun, uuid.UUID(run_id))
        row.output_json = {"seed": 1}
        db.commit()
    assert client.get(_url(pid, plan_id, run_id)).status_code == 409
