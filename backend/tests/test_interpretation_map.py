"""Findings-to-literature map, G9 review and gate wiring (M6.9, spec 5.10 and 8).

Covers `app.interpretation_map` (DB-aware generation/review), the new router
(`app/routers/interpretation.py`), and the real EC-12.2/EC-12.3 checks that now block G9's approval
(`app/exit_conditions.py`, `routers/gates.py::_OWN_GATE_ROW`).
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import exit_conditions
from app.database import SessionLocal
from app.main import app
from app.models import (
    AnalysisPlan,
    AnalysisRun,
    DatasetVersion,
    Extraction,
    Gate,
    GateCode,
    InterpretationFinding,
    Project,
    ProjectStage,
    Source,
    utcnow,
)
from gate_helpers import approve_earlier_gates


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, user_id


def _project(client, title="Interpretation project"):
    return client.post("/api/projects", json={"title": title}).json()["id"]


def _prep(project_id: str, *, actor="test-setup", output_json=None, field_value=0.6, literature_value=0.3, run_before_g8=False):
    """Put the project at plan_locked with G1-G8 approved, one analysis run and one verified extraction
    whose fields overlap the run's output keys, so a map can be generated and classified. The run's
    `created_at` is set explicitly relative to G8's `decided_at` (after, unless `run_before_g8`) rather
    than left to a server-side clock, so EC-12.2 ("run executed after G8 approved") is unambiguous
    regardless of the dialect's timestamp resolution."""
    pid = uuid.UUID(str(project_id))
    output_json = output_json if output_json is not None else {"results": {"pearson_r": field_value}}
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.stage = ProjectStage.plan_locked
        dataset = DatasetVersion(
            project_id=pid, version_no=1, kind="raw", name="d1", content="x,y\n1,2\n", sha256="a" * 64,
            row_count=1, column_names=["x", "y"], created_by=actor,
        )
        db.add(dataset)
        db.flush()
        plan = AnalysisPlan(
            project_id=pid, dataset_id=dataset.id, spec_json={"tests": ["correlations"], "variables": ["x", "y"], "seed": 1},
            plan_sha256="b" * 64, environment_sha256="c" * 64, locked_at=utcnow(), locked_by=actor, created_by=actor,
        )
        db.add(plan)
        source = Source(project_id=pid, title="Prior study", source_type="article", metadata_verified=True, created_by=actor)
        db.add(source)
        db.flush()
        extraction = Extraction(
            project_id=pid, source_id=source.id, schema_version="demo@1",
            fields_json={"pearson_r": literature_value}, evidence_spans={}, verified_by_human=True,
            verified_by=actor, verified_at=utcnow(), extracted_by="human", extractor=actor,
        )
        db.add(extraction)
        db.commit()
        dataset_id, plan_id = dataset.id, plan.id
    approve_earlier_gates(project_id, GateCode.G9)
    with SessionLocal() as db:
        g8 = db.scalar(select(Gate).where(Gate.project_id == pid, Gate.code == GateCode.G8))
        run_created_at = g8.decided_at - timedelta(days=1) if run_before_g8 else g8.decided_at + timedelta(days=1)
        run = AnalysisRun(
            project_id=pid, analysis_plan_id=plan_id, dataset_id=dataset_id, plan_sha256="b" * 64,
            dataset_sha256="a" * 64, script_sha256="d" * 64, environment_sha256="c" * 64, image="sandbox:1",
            output_json=output_json, output_sha256="e" * 64, created_by=actor, created_at=run_created_at,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    return run_id


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    client, user_id = _login(f"interp-{tag}@example.com")
    project_id = _project(client)
    return client, project_id, user_id


# ---- generation --------------------------------------------------------------------------------


def test_generate_classifies_a_matching_finding_as_agree(world):
    client, project_id, _ = world
    _prep(project_id)
    response = client.post(f"/api/projects/{project_id}/interpretation/generate")
    assert response.status_code == 201, response.text
    findings = response.json()
    assert len(findings) == 1
    assert findings[0]["output_key"] == "pearson_r"
    assert findings[0]["output_path"] == "results.pearson_r"
    assert findings[0]["classification"] == "agree"
    assert findings[0]["confirmed"] is False


def test_generate_with_no_analysis_run_is_refused(world):
    client, project_id, _ = world
    response = client.post(f"/api/projects/{project_id}/interpretation/generate")
    assert response.status_code == 409


def test_regenerating_writes_a_new_batch_not_an_edit(world):
    client, project_id, _ = world
    _prep(project_id)
    first = client.post(f"/api/projects/{project_id}/interpretation/generate").json()
    second = client.post(f"/api/projects/{project_id}/interpretation/generate").json()
    assert {f["id"] for f in first}.isdisjoint({f["id"] for f in second})
    with SessionLocal() as db:
        assert db.scalar(select(InterpretationFinding).where(InterpretationFinding.id == uuid.UUID(first[0]["id"]))) is not None
    # Listing only ever returns the newest batch.
    listed = client.get(f"/api/projects/{project_id}/interpretation/findings").json()
    assert {f["id"] for f in listed} == {f["id"] for f in second}


def test_opposite_sign_literature_classifies_as_disagree(world):
    client, project_id, _ = world
    _prep(project_id, field_value=0.6, literature_value=-0.4)
    findings = client.post(f"/api/projects/{project_id}/interpretation/generate").json()
    assert findings[0]["classification"] == "disagree"


# ---- review --------------------------------------------------------------------------------


def test_list_findings_is_empty_before_any_generation(world):
    client, project_id, _ = world
    assert client.get(f"/api/projects/{project_id}/interpretation/findings").json() == []


def test_reviewing_an_unknown_finding_is_404(world):
    client, project_id, _ = world
    response = client.post(
        f"/api/projects/{project_id}/interpretation/findings/{uuid.uuid4()}/review", json={"decision": "confirmed"}
    )
    assert response.status_code == 404


def test_confirming_a_finding_is_reflected_in_the_list(world):
    client, project_id, _ = world
    _prep(project_id)
    finding = client.post(f"/api/projects/{project_id}/interpretation/generate").json()[0]
    review = client.post(
        f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review",
        json={"decision": "confirmed", "note": "checked against the paper"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["confirmed"] is True
    listed = client.get(f"/api/projects/{project_id}/interpretation/findings").json()
    assert listed[0]["confirmed"] is True


def test_a_later_dispute_overrides_an_earlier_confirm(world):
    client, project_id, _ = world
    _prep(project_id)
    finding = client.post(f"/api/projects/{project_id}/interpretation/generate").json()[0]
    client.post(f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review", json={"decision": "confirmed"})
    second = client.post(f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review", json={"decision": "disputed"})
    assert second.json()["confirmed"] is False


def test_interpretation_review_rows_cannot_be_edited(world):
    client, project_id, _ = world
    _prep(project_id)
    finding = client.post(f"/api/projects/{project_id}/interpretation/generate").json()[0]
    client.post(f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review", json={"decision": "confirmed"})
    with SessionLocal() as db:
        row = db.scalar(select(InterpretationFinding).where(InterpretationFinding.id == uuid.UUID(finding["id"])))
        row.rationale = "edited"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()


# ---- draft --------------------------------------------------------------------------------


def test_draft_builds_a_suggestion_with_stat_and_cite_nodes_never_auto_accepted(world):
    client, project_id, _ = world
    _prep(project_id)
    client.post(f"/api/projects/{project_id}/interpretation/generate")
    response = client.post(f"/api/projects/{project_id}/interpretation/draft")
    assert response.status_code == 201, response.text
    body = response.json()
    paragraph = body["content"]["content"][0]
    types = [node["type"] for node in paragraph["content"]]
    assert "stat" in types and "cite" in types
    suggestions = client.get(
        f"/api/projects/{project_id}/suggestions", params={"target_kind": "project", "target_id": project_id}
    ).json()
    suggestion = next(s for s in suggestions if s["id"] == body["suggestion_id"])
    assert suggestion["model_id"] == "deterministic"
    assert suggestion["state"] == "pending"


def test_draft_with_no_map_generated_is_refused(world):
    client, project_id, _ = world
    response = client.post(f"/api/projects/{project_id}/interpretation/draft")
    assert response.status_code == 409


# ---- G9 gate wiring (EC-12.2, EC-12.3) ------------------------------------------------------


def test_ec_12_3_is_unmet_until_every_finding_is_confirmed(world):
    client, project_id, _ = world
    _prep(project_id)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        assert exit_conditions.ec_12_3(db, project).met is False
    client.post(f"/api/projects/{project_id}/interpretation/generate")
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        assert exit_conditions.ec_12_3(db, project).met is False  # generated, not yet confirmed
    finding = client.get(f"/api/projects/{project_id}/interpretation/findings").json()[0]
    client.post(f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review", json={"decision": "confirmed"})
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        assert exit_conditions.ec_12_3(db, project).met is True


def test_g9_approval_is_refused_while_findings_are_unconfirmed(world):
    client, project_id, _ = world
    _prep(project_id)
    client.post(f"/api/projects/{project_id}/interpretation/generate")
    response = client.post(f"/api/projects/{project_id}/gates/G9/approve")
    assert response.status_code == 409, response.text
    unmet_ids = [item["id"] for item in response.json()["detail"]["required"]]
    assert "EC-12.3" in unmet_ids


def test_g9_approves_once_every_finding_is_confirmed_and_runs_postdate_g8(world):
    client, project_id, _ = world
    _prep(project_id)
    client.post(f"/api/projects/{project_id}/interpretation/generate")
    for finding in client.get(f"/api/projects/{project_id}/interpretation/findings").json():
        client.post(f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review", json={"decision": "confirmed"})
    response = client.post(f"/api/projects/{project_id}/gates/G9/approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"


def test_ec_12_2_is_unmet_when_the_only_run_predates_g8s_approval(world):
    client, project_id, _ = world
    _prep(project_id, run_before_g8=True)
    client.post(f"/api/projects/{project_id}/interpretation/generate")
    for finding in client.get(f"/api/projects/{project_id}/interpretation/findings").json():
        client.post(f"/api/projects/{project_id}/interpretation/findings/{finding['id']}/review", json={"decision": "confirmed"})
    response = client.post(f"/api/projects/{project_id}/gates/G9/approve")
    assert response.status_code == 409, response.text
    unmet_ids = [item["id"] for item in response.json()["detail"]["required"]]
    assert "EC-12.2" in unmet_ids


def test_ec_12_2_is_unmet_before_g8_is_approved():
    tag = uuid.uuid4().hex[:8]
    client, user_id = _login(f"interp-g8-{tag}@example.com")
    project_id = _project(client)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        condition = exit_conditions.ec_12_2(db, project)
        assert condition.met is False
        assert "G8 has not been approved" in condition.message


# ---- role scoping --------------------------------------------------------------------------


def test_a_reviewer_cannot_generate_or_review_findings(world):
    client, project_id, user_id = world
    _prep(project_id)
    reviewer_email = f"interp-reviewer-{uuid.uuid4().hex[:8]}@example.com"
    reviewer, reviewer_id = _login(reviewer_email)
    client.post(f"/api/projects/{project_id}/members", json={"email": reviewer_email, "role": "reviewer"})
    assert reviewer.post(f"/api/projects/{project_id}/interpretation/generate").status_code == 403
    findings = client.post(f"/api/projects/{project_id}/interpretation/generate").json()
    assert reviewer.post(
        f"/api/projects/{project_id}/interpretation/findings/{findings[0]['id']}/review", json={"decision": "confirmed"}
    ).status_code == 403


def test_a_reviewer_can_read_findings(world):
    client, project_id, user_id = world
    _prep(project_id)
    client.post(f"/api/projects/{project_id}/interpretation/generate")
    reviewer_email = f"interp-reader-{uuid.uuid4().hex[:8]}@example.com"
    reviewer, reviewer_id = _login(reviewer_email)
    client.post(f"/api/projects/{project_id}/members", json={"email": reviewer_email, "role": "reviewer"})
    assert reviewer.get(f"/api/projects/{project_id}/interpretation/findings").status_code == 200
