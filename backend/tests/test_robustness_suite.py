import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import analysis_runner
from app import robustness_suite as suite
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, DatasetVersion
from app.routers import analysis_plan as router_module

CSV = "x,y\n1,2\n2,4.5\n3,5\n4,8\n"
OTHER_CSV = "x,y\n1,3\n2,4\n3,9\n4,7\n"
DIGEST = "python@sha256:" + "c" * 64
SPEC = {"tests": ["descriptives", "correlations"], "variables": ["x", "y"], "seed": 1}


# ---- pure spec-variant helper -----------------------------------------------------------

def test_leave_one_out_drops_correlations_when_only_one_variable_would_remain():
    alts = suite.leave_one_out_specs(SPEC)
    labels = {a["label"] for a in alts}
    assert labels == {"drop x", "drop y"}
    drop_x = next(a for a in alts if a["label"] == "drop x")
    assert drop_x["spec"] == {"tests": ["descriptives"], "variables": ["y"], "seed": 1}


def test_leave_one_out_has_nothing_to_offer_for_a_single_variable_or_correlations_only_pair():
    assert suite.leave_one_out_specs({"tests": ["descriptives"], "variables": ["x"], "seed": 1}) == []
    assert suite.leave_one_out_specs({"tests": ["correlations"], "variables": ["x", "y"], "seed": 1}) == []


def test_leave_one_out_keeps_descriptives_when_three_variables_drop_to_two():
    spec = {"tests": ["descriptives", "correlations"], "variables": ["x", "y", "z"], "seed": 1}
    alts = {a["label"]: a["spec"] for a in suite.leave_one_out_specs(spec)}
    assert set(alts["drop z"]["variables"]) == {"x", "y"}
    assert alts["drop z"]["tests"] == ["descriptives", "correlations"]  # two variables left, correlations still valid


# ---- API: robustness checks and notebook export -----------------------------------------

def _settings(image):
    return lambda: SimpleNamespace(analysis_sandbox_image=image, analysis_sandbox_timeout_seconds=60)


def _local_python(argv, env, timeout):
    """Stands in for Docker: runs the same script and file names locally (same stand-in as test_analysis_run_api.py)."""
    host_dir = argv[argv.index("-v") + 1].rsplit(":/work", 1)[0]
    return subprocess.run([sys.executable, "-I", os.path.join(host_dir, "analysis.py"), os.path.join(host_dir, "dataset.csv")],
                          capture_output=True, text=True, timeout=timeout, env=analysis_runner.cli_env(os.environ))


@pytest.fixture()
def run(monkeypatch):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"rb-{uuid.uuid4().hex}@example.com", "display_name": "R"})
    pid = client.post("/api/projects", json={"title": "Robustness"}).json()["id"]
    ds = client.post(f"/api/projects/{pid}/datasets", json={"name": "wave", "csv_text": CSV}).json()
    plan = client.post(f"/api/projects/{pid}/datasets/{ds['id']}/analysis-plans",
                       json={"tests": ["descriptives", "correlations"], "variables": ["x", "y"], "seed": 1}).json()
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan['id']}/lock").status_code == 200
    monkeypatch.setattr(router_module, "get_settings", _settings(DIGEST))
    monkeypatch.setattr(analysis_runner, "_default_executor", _local_python)
    run_body = client.post(f"/api/projects/{pid}/analysis-plans/{plan['id']}/runs").json()
    return client, pid, ds["id"], plan["id"], run_body["id"]


def test_rerun_identity_matches_the_stored_run(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness", json={"kind": "rerun_identity"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["identical"] is True
    assert body["base_fingerprint"] == body["compare_fingerprint"]
    assert body["label"] == "unchanged re-run"


def test_alternative_spec_drops_a_variable_and_differs_from_the_base_run(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                     json={"kind": "alternative_spec", "drop_variable": "y"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "drop y"
    assert body["identical"] is False  # descriptives+correlations over {x,y} vs descriptives only over {x}: different shape
    compare_plan = client.get(f"/api/projects/{pid}/analysis-plans/{body['compare_plan_id']}").json()
    assert compare_plan["spec"]["variables"] == ["x"]


def test_alternative_spec_refuses_a_variable_that_is_not_in_the_plan(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                     json={"kind": "alternative_spec", "drop_variable": "nope"})
    assert r.status_code == 422 and "nope" in r.json()["detail"]


def test_alternative_spec_without_a_variable_is_422(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness", json={"kind": "alternative_spec"})
    assert r.status_code == 422


def test_outlier_sensitivity_against_the_same_dataset_is_refused(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                     json={"kind": "outlier_sensitivity", "compare_dataset_id": dataset_id})
    assert r.status_code == 422 and "different dataset version" in r.json()["detail"]


def test_outlier_sensitivity_without_a_dataset_is_422(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness", json={"kind": "outlier_sensitivity"})
    assert r.status_code == 422


def test_comparison_dataset_missing_a_required_column_is_refused(run):
    client, pid, dataset_id, plan_id, run_id = run
    other = client.post(f"/api/projects/{pid}/datasets", json={"name": "other", "csv_text": "x,z\n1,2\n"}).json()
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                     json={"kind": "missing_data", "compare_dataset_id": other["id"]})
    assert r.status_code == 422 and "missing column" in r.json()["detail"]


def test_outlier_sensitivity_against_a_cleaned_sibling_dataset_runs_and_compares(run):
    client, pid, dataset_id, plan_id, run_id = run
    rule = client.post(f"/api/projects/{pid}/datasets/{dataset_id}/exclusion-rules", json={"column": "y", "op": "greater_than", "value": "7"})
    assert rule.status_code == 201, rule.text
    assert client.post(f"/api/projects/{pid}/datasets/{dataset_id}/exclusion-rules/{rule.json()['id']}/approve").status_code == 200
    cleaned = client.post(f"/api/projects/{pid}/datasets/{dataset_id}/exclude",
                          json={"name": "no-outlier", "rule_ids": [rule.json()["id"]]})
    assert cleaned.status_code == 201, cleaned.text
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                     json={"kind": "outlier_sensitivity", "compare_dataset_id": cleaned.json()["id"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["identical"] is False  # one row dropped, so the recorded n differs
    assert body["compare_dataset_id"] == cleaned.json()["id"]


def test_a_tampered_comparison_dataset_is_refused(run):
    client, pid, dataset_id, plan_id, run_id = run
    other = client.post(f"/api/projects/{pid}/datasets", json={"name": "other", "csv_text": CSV}).json()
    with SessionLocal() as db:
        version = db.get(DatasetVersion, uuid.UUID(other["id"]))
        version.content = version.content.replace("1,2", "9,9")
        db.commit()
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                     json={"kind": "missing_data", "compare_dataset_id": other["id"]})
    assert r.status_code == 409


def test_robustness_checks_are_stored_listed_and_audited(run):
    client, pid, dataset_id, plan_id, run_id = run
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness",
                        json={"kind": "rerun_identity"}).status_code == 201
    listed = client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness").json()
    assert len(listed) == 1 and listed[0]["kind"] == "rerun_identity"
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "robustness.checked")).all()
        assert events


def test_robustness_check_refuses_once_g8_is_approved(run):
    from app.models import Gate, GateCode, GateStatus
    client, pid, dataset_id, plan_id, run_id = run
    with SessionLocal() as db:
        gate = db.query(Gate).filter(Gate.project_id == uuid.UUID(pid), Gate.code == GateCode.G8).one()
        gate.status = GateStatus.approved
        db.commit()
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/robustness", json={"kind": "rerun_identity"})
    assert r.status_code == 409


def test_a_run_that_does_not_exist_is_404(run):
    client, pid, dataset_id, plan_id, run_id = run
    bad = str(uuid.uuid4())
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{bad}/robustness",
                        json={"kind": "rerun_identity"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{bad}/robustness").status_code == 404
    assert client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{bad}/notebook").status_code == 404


# ---- notebook export ----------------------------------------------------------------------

def test_notebook_packages_the_script_dataset_and_recorded_output(run):
    client, pid, dataset_id, plan_id, run_id = run
    r = client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs/{run_id}/notebook")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"].endswith(".ipynb")
    notebook = body["notebook"]
    assert notebook["nbformat"] == 4
    joined = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    assert "SPEC = json.loads(" in joined  # the generated script is embedded verbatim
    assert CSV.splitlines()[1] in joined  # the dataset content is embedded
    assert '"n": 4' in joined  # the recorded output JSON is shown


def test_notebook_is_scoped_to_its_project(run):
    client, pid, dataset_id, plan_id, run_id = run
    client.post("/api/auth/development/login", json={"email": f"other-{uuid.uuid4().hex}@example.com", "display_name": "O"})
    other_pid = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    assert client.get(f"/api/projects/{other_pid}/analysis-plans/{plan_id}/runs/{run_id}/notebook").status_code == 404
