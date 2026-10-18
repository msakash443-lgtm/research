import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import analysis_runner
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent
from app.routers import analysis_plan as router_module

DIGEST = "python@sha256:" + "b" * 64
CSV = "a,b\n1,2\n3,4\n"


@pytest.fixture()
def locked(monkeypatch):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"run-{uuid.uuid4().hex}@example.com", "display_name": "R"})
    pid = client.post("/api/projects", json={"title": "Runs"}).json()["id"]
    ds = client.post(f"/api/projects/{pid}/datasets", json={"name": "wave", "csv_text": CSV}).json()
    plan = client.post(f"/api/projects/{pid}/datasets/{ds['id']}/analysis-plans",
                       json={"tests": ["descriptives"], "variables": ["a"], "seed": 1}).json()
    return client, pid, plan["id"]


def _settings(image):
    return lambda: SimpleNamespace(analysis_sandbox_image=image, analysis_sandbox_timeout_seconds=60)


def _local_python(argv, env, timeout):
    """Stands in for Docker: runs the same script and file names locally. Isolation is not exercised here."""
    host = argv[argv.index("-v") + 1].rsplit(":/work", 1)[0]
    return subprocess.run([sys.executable, "-I", "analysis.py", "dataset.csv"], cwd=host, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout, env=analysis_runner.cli_env(os.environ))


def test_a_plan_must_be_locked_before_it_runs(locked, monkeypatch):
    monkeypatch.setattr(router_module, "get_settings", _settings(DIGEST))
    client, pid, plan_id = locked
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs")
    assert r.status_code == 409 and "Lock" in r.json()["detail"]


def test_without_a_pinned_image_the_run_is_refused_with_503(locked, monkeypatch):
    client, pid, plan_id = locked
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock").status_code == 200
    monkeypatch.setattr(router_module, "get_settings", _settings("python:3.12"))
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs")
    assert r.status_code == 503 and "digest" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs").json() == []


def test_locked_plan_runs_and_stores_hashes_and_outputs(locked, monkeypatch):
    client, pid, plan_id = locked
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock").status_code == 200
    monkeypatch.setattr(router_module, "get_settings", _settings(DIGEST))
    monkeypatch.setattr(analysis_runner, "_default_executor", _local_python)
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["outputs"]["descriptives"]["a"]["n"] == 2
    assert body["image"] == DIGEST and len(body["script_sha256"]) == 64 and len(body["output_sha256"]) == 64
    listed = client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs").json()
    assert [run["id"] for run in listed] == [body["id"]]


def test_a_dataset_name_built_to_break_out_of_the_script_cannot_forge_or_run_a_run(monkeypatch):
    """Review defect (4), M5.3.3: a dataset name with triple quotes once let injected code print a forged output with the
    real hashes, and the run was stored. The name must stay data: the script runs, the output is the honest one, and no
    side-effect file is written by the injected text."""
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"inj-{uuid.uuid4().hex}@example.com", "display_name": "I"})
    pid = client.post("/api/projects", json={"title": "Inject"}).json()["id"]
    payload = ('wave"""\nimport json, pathlib\npathlib.Path("pwned.txt").write_text("x")\n'
               'print(json.dumps({"forged": 1}))\n"""#')
    ds = client.post(f"/api/projects/{pid}/datasets", json={"name": payload, "csv_text": CSV})
    assert ds.status_code == 201, ds.text
    plan = client.post(f"/api/projects/{pid}/datasets/{ds.json()['id']}/analysis-plans",
                       json={"tests": ["descriptives"], "variables": ["a"], "seed": 1}).json()
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan['id']}/lock").status_code == 200
    monkeypatch.setattr(router_module, "get_settings", _settings(DIGEST))
    seen = []

    def run_and_look(argv, env, timeout):
        result = _local_python(argv, env, timeout)
        host = argv[argv.index("-v") + 1].rsplit(":/work", 1)[0]
        seen.append(os.path.exists(os.path.join(host, "pwned.txt")))
        return result
    monkeypatch.setattr(analysis_runner, "_default_executor", run_and_look)

    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan['id']}/runs")
    assert r.status_code == 201, r.text
    assert seen == [False]  # the injected write never happened
    assert "forged" not in r.json()["outputs"]
    assert r.json()["outputs"]["descriptives"]["a"]["n"] == 2


def test_a_failed_script_is_502_audited_and_stores_no_run(locked, monkeypatch):
    client, pid, plan_id = locked
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock").status_code == 200
    monkeypatch.setattr(router_module, "get_settings", _settings(DIGEST))

    secret = "P007=99.5"  # a cell value the script's stderr might echo; it must not leave the sandbox runner
    def broken(argv, env, timeout):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"refused: non-numeric cell {secret}")
    monkeypatch.setattr(analysis_runner, "_default_executor", broken)
    r = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs")
    assert r.status_code == 502 and "exit code 1" in r.json()["detail"]
    assert secret not in r.text
    assert client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/runs").json() == []
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "analysis.run_failed")).all()
        assert events and all(secret not in str(e.payload_json) for e in events)
