import ast
import json
import statistics
import subprocess
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from app import analysis_plan as rules
from app import datasets as ds
from app.database import SessionLocal
from app.main import app
from app.models import AnalysisPlan, DatasetVersion, Gate, GateCode, GateStatus

CSV = "x,y,label\n1,2,a\n2,4.5,b\n3,5,c\n4,8,d\n"
COLUMNS = ["x", "y", "label"]
SPEC = {"tests": ["descriptives", "correlations"], "variables": ["x", "y"], "seed": 7}


# ---- pure rules -----------------------------------------------------------------------

def test_validate_accepts_a_good_spec_and_returns_it_normalised():
    assert rules.validate(SPEC, COLUMNS) == SPEC


@pytest.mark.parametrize("spec, message", [
    ({**SPEC, "extra": 1}, "Unknown plan field"),
    ({**SPEC, "tests": []}, "at least one test"),
    ({**SPEC, "tests": ["descriptives", "descriptives"]}, "once"),
    ({**SPEC, "tests": ["regression"]}, "Unknown test"),
    ({**SPEC, "variables": []}, "at least one variable"),
    ({**SPEC, "variables": ["x", "x"]}, "once"),
    ({**SPEC, "variables": ["x", "nope"]}, "Not a column"),
    ({**SPEC, "variables": ["x"]}, "at least two variables"),
    ({**SPEC, "seed": -1}, "seed"),
    ({**SPEC, "seed": True}, "seed"),
    ({**SPEC, "seed": rules.MAX_SEED + 1}, "seed"),
])
def test_validate_refuses_bad_specs_with_a_reason(spec, message):
    with pytest.raises(rules.PlanError, match=message):
        rules.validate(spec, COLUMNS)


def test_plan_hash_is_stable_and_changes_with_the_spec_or_the_dataset():
    base = rules.plan_hash(SPEC, "abc")
    assert base == rules.plan_hash(dict(reversed(list(SPEC.items()))), "abc")  # key order does not matter
    assert base != rules.plan_hash({**SPEC, "seed": 8}, "abc")
    assert base != rules.plan_hash(SPEC, "abd")


def test_generated_script_is_deterministic_and_embeds_the_hashes():
    sha = ds.sha256_hex(CSV)
    first = rules.generate_script(SPEC, "survey", sha)
    assert first == rules.generate_script(SPEC, "survey", sha)
    assert sha in first
    assert rules.plan_hash(SPEC, sha) in first
    assert rules.environment_hash() in first


def test_generated_script_imports_only_the_standard_library_allow_list():
    script = rules.generate_script(SPEC, "survey", ds.sha256_hex(CSV))
    assert rules.imported_modules(script) <= rules.ALLOWED_IMPORTS
    ast.parse(script)


def test_dataset_names_cannot_inject_code_or_rewrite_other_tokens():
    tricky = "x'); import os; print('__SPEC__"
    script = rules.generate_script(SPEC, tricky, ds.sha256_hex(CSV))
    assert "os" not in rules.imported_modules(script)
    assert "SPEC = json.loads(" in script and repr(tricky) in script


def test_dataset_name_with_triple_quotes_cannot_break_out_of_the_docstring(tmp_path):
    # Review defect (M5.3.2): the name was spliced into the docstring, so a name with a triple quote and no
    # single quote ran as code. The marker file must never appear, and the name must come back as plain data.
    marker = tmp_path / "pwned.txt"
    tricky = f'"""\nopen({str(marker)!r}, "w").write("ran")\n"""'
    script = rules.generate_script(SPEC, tricky, ds.sha256_hex(CSV))
    assert rules.imported_modules(script) <= rules.ALLOWED_IMPORTS
    done = _run(script, tmp_path, CSV)
    assert done.returncode == 0, done.stderr
    assert not marker.exists()
    assert json.loads(done.stdout)["dataset"] == tricky


def _run(script_text, tmp_path, csv_text):
    script = tmp_path / "analysis.py"
    script.write_text(script_text, encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_bytes(csv_text.encode("utf-8"))
    # Synthetic fixture data only: this exercises the generated code in a test, not a platform run (M5.3.3).
    return subprocess.run([sys.executable, "-I", str(script), str(data)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)


def test_generated_script_computes_the_descriptives_and_correlation(tmp_path):
    sha = ds.sha256_hex(CSV)
    done = _run(rules.generate_script(SPEC, "survey", sha), tmp_path, CSV)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    xs, ys = [1.0, 2.0, 3.0, 4.0], [2.0, 4.5, 5.0, 8.0]
    assert out["descriptives"]["x"]["mean"] == pytest.approx(statistics.fmean(xs))
    assert out["descriptives"]["x"]["sd"] == pytest.approx(statistics.stdev(xs))
    assert out["descriptives"]["y"]["max"] == 8.0
    corr = {(p["a"], p["b"]): p["r"] for p in out["correlations"]}
    assert corr[("x", "y")] == pytest.approx(statistics.correlation(xs, ys))
    assert out["dataset_sha256"] == sha and out["seed"] == 7


@pytest.mark.parametrize("cell", ["nan", "NaN", "inf", "-inf", "Infinity", "1_0", "1e999"])
def test_generated_script_refuses_cells_that_are_not_plain_finite_numbers(tmp_path, cell):
    # Review defect (M5.3.2.b): float() accepts these, so they were read as numbers. They must refuse, not be kept.
    csv_text = f"x,y,label\n1,2,a\n{cell},4.5,b\n3,5,c\n"
    done = _run(rules.generate_script(SPEC, "survey", ds.sha256_hex(csv_text)), tmp_path, csv_text)
    assert done.returncode != 0
    assert "not a number" in done.stderr
    assert done.stdout == ""


def test_correlation_pairs_with_tilde_in_names_do_not_collide(tmp_path):
    # Review defect (M5.3.2.a): keys "a~b" collided for names ("p~q", "r") and ("p", "q~r"), giving 5 results for 6 pairs.
    cols = ["p~q", "r", "p", "q~r"]
    csv_text = "p~q,r,p,q~r\n1,2,3,4\n2,3,1,5\n3,1,2,2\n4,4,6,1\n"
    spec = {"tests": ["correlations"], "variables": cols, "seed": 1}
    done = _run(rules.generate_script(spec, "t", ds.sha256_hex(csv_text)), tmp_path, csv_text)
    assert done.returncode == 0, done.stderr
    pairs = json.loads(done.stdout)["correlations"]
    assert len(pairs) == 6
    assert len({(p["a"], p["b"]) for p in pairs}) == 6


def test_generated_script_refuses_a_changed_file(tmp_path):
    done = _run(rules.generate_script(SPEC, "survey", ds.sha256_hex(CSV)), tmp_path, CSV.replace("8", "9"))
    assert done.returncode != 0
    assert "does not match" in done.stderr


def test_generated_script_refuses_missing_cells_rather_than_dropping_them(tmp_path):
    holey = "x,y\n1,2\n,4\n3,5\n"
    done = _run(rules.generate_script({"tests": ["descriptives"], "variables": ["x"], "seed": 0}, "s", ds.sha256_hex(holey)), tmp_path, holey)
    assert done.returncode != 0
    assert "not a number" in done.stderr


# ---- API ------------------------------------------------------------------------------

@pytest.fixture
def owner():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"ap-{uuid.uuid4().hex}@example.com", "display_name": "P"})
    pid = client.post("/api/projects", json={"title": "Plans"}).json()["id"]
    return client, pid


def _dataset(client, pid, text=CSV):
    response = client.post(f"/api/projects/{pid}/datasets", json={"name": "survey", "csv_text": text})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _plan(client, pid, dataset_id, **overrides):
    body = {"tests": ["descriptives"], "variables": ["x"], "seed": 1, **overrides}
    return client.post(f"/api/projects/{pid}/datasets/{dataset_id}/analysis-plans", json=body)


def _set_gate(pid, status):
    with SessionLocal() as db:
        gate = db.query(Gate).filter_by(project_id=uuid.UUID(pid), code=GateCode.G8).one()
        gate.status = status
        db.commit()


def test_a_plan_records_its_hashes_and_is_not_locked_until_lock(owner):
    client, pid = owner
    dataset_id = _dataset(client, pid)
    created = _plan(client, pid, dataset_id, variables=["x", "y"], tests=["correlations"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["plan_sha256"] == rules.plan_hash(body["spec"], ds.sha256_hex(CSV))
    assert body["environment_sha256"] == rules.environment_hash()
    assert body["plan_intact"] is True and body["dataset_intact"] is True
    assert body["locked"] is False and body["locked_at"] is None
    assert client.get(f"/api/projects/{pid}/analysis-plans/{body['id']}").json()["spec"]["variables"] == ["x", "y"]


def test_a_variable_that_is_not_a_column_is_422(owner):
    client, pid = owner
    assert _plan(client, pid, _dataset(client, pid), variables=["missing"]).status_code == 422


def test_a_plan_cannot_use_a_dataset_altered_outside_the_api(owner):
    client, pid = owner
    dataset_id = _dataset(client, pid)
    with SessionLocal() as db:
        db.get(DatasetVersion, uuid.UUID(dataset_id)).content = "x,y,label\n1,9,a\n"
        db.commit()
    assert _plan(client, pid, dataset_id).status_code == 409


def test_the_script_is_served_only_for_an_intact_plan_and_matches_the_generator(owner):
    client, pid = owner
    dataset_id = _dataset(client, pid)
    plan_id = _plan(client, pid, dataset_id, tests=["descriptives"], variables=["x", "y"], seed=3).json()["id"]
    got = client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/script")
    assert got.status_code == 200, got.text
    expected = rules.generate_script({"tests": ["descriptives"], "variables": ["x", "y"], "seed": 3}, "survey", ds.sha256_hex(CSV))
    assert got.json()["script"] == expected
    assert got.json()["filename"].endswith(".py")


def test_a_tampered_plan_gives_no_script_and_cannot_be_locked(owner):
    client, pid = owner
    plan_id = _plan(client, pid, _dataset(client, pid)).json()["id"]
    with SessionLocal() as db:
        db.get(AnalysisPlan, uuid.UUID(plan_id)).spec_json = {"tests": ["descriptives"], "variables": ["y"], "seed": 1}
        db.commit()
    assert client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}/script").status_code == 409
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock").status_code == 409


def test_lock_refuses_a_dataset_whose_content_was_altered_after_the_plan_was_made(owner):
    # The stored dataset.sha256 still matches the plan here; only the content has changed. Lock must check the content.
    client, pid = owner
    dataset_id = _dataset(client, pid)
    plan_id = _plan(client, pid, dataset_id).json()["id"]
    with SessionLocal() as db:
        db.get(DatasetVersion, uuid.UUID(dataset_id)).content = "x,y,label\n1,9,a\n"
        db.commit()
    refused = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock")
    assert refused.status_code == 409, refused.text
    after = client.get(f"/api/projects/{pid}/analysis-plans/{plan_id}").json()
    assert after["locked"] is False and after["dataset_intact"] is False


def test_lock_freezes_the_plan_and_is_idempotent(owner):
    client, pid = owner
    plan_id = _plan(client, pid, _dataset(client, pid)).json()["id"]
    first = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock")
    assert first.status_code == 200, first.text
    assert first.json()["locked"] is True and first.json()["locked_by"]
    second = client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock")
    assert second.json()["locked_at"] == first.json()["locked_at"]


def test_once_g8_is_approved_no_plan_can_be_created_or_locked(owner):
    client, pid = owner
    dataset_id = _dataset(client, pid)
    plan_id = _plan(client, pid, dataset_id).json()["id"]
    _set_gate(pid, GateStatus.approved)
    assert _plan(client, pid, dataset_id).status_code == 409
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock").status_code == 409


def test_creating_and_locking_are_audited(owner):
    from app.models import AuditEvent

    client, pid = owner
    plan_id = _plan(client, pid, _dataset(client, pid)).json()["id"]
    client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock")
    with SessionLocal() as db:
        actions = {e.action for e in db.query(AuditEvent).filter_by(project_id=uuid.UUID(pid)).all()}
    assert {"analysis_plan.created", "analysis_plan.locked"} <= actions


def test_plans_are_scoped_to_their_project(owner):
    client, pid = owner
    plan_id = _plan(client, pid, _dataset(client, pid)).json()["id"]
    other = client.post("/api/projects", json={"title": "Elsewhere"}).json()["id"]
    assert client.get(f"/api/projects/{other}/analysis-plans/{plan_id}").status_code == 404


# ---- M5.3.4: G8 approval needs a locked plan ------------------------------------------

def _approve_gates_before_g8(pid):
    from app.models import GateStatus

    with SessionLocal() as db:
        for gate in db.query(Gate).filter_by(project_id=uuid.UUID(pid)).all():
            if gate.code in (GateCode.G1, GateCode.G2, GateCode.G3, GateCode.G4, GateCode.G5, GateCode.G6, GateCode.G7):
                gate.status = GateStatus.approved
        db.commit()


def test_g8_approval_is_refused_without_a_locked_plan_and_allowed_after_lock(owner):
    client, pid = owner
    _approve_gates_before_g8(pid)
    refused = client.post(f"/api/projects/{pid}/gates/G8/approve")
    assert refused.status_code == 409
    assert "locked" in refused.json()["detail"]

    dataset_id = _dataset(client, pid)
    plan_id = _plan(client, pid, dataset_id).json()["id"]
    assert client.post(f"/api/projects/{pid}/gates/G8/approve").status_code == 409  # created but not locked: still refused
    assert client.post(f"/api/projects/{pid}/analysis-plans/{plan_id}/lock").status_code == 200

    approved = client.post(f"/api/projects/{pid}/gates/G8/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"


def test_g8_refusal_changes_nothing(owner):
    client, pid = owner
    _approve_gates_before_g8(pid)
    client.post(f"/api/projects/{pid}/gates/G8/approve")
    with SessionLocal() as db:
        gate = db.query(Gate).filter_by(project_id=uuid.UUID(pid), code=GateCode.G8).one()
        assert gate.status != "approved"
