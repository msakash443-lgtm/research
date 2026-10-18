"""Link-check of inserted numbers in a manuscript section (M6.3): the route reports a `stat` node that names an
analysis run this project doesn't have, or a path that doesn't resolve to a number in that run's output; it changes
nothing and does not block export (that hard block is `app.manuscript_guard`/export)."""

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AnalysisPlan, AnalysisRun, DatasetVersion, DocumentVersion

OUTPUT = {"descriptives": {"age": {"n": 40, "mean": 35.5}}}


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "N"}).json()["id"]
    return client, user_id


def _project(client):
    return client.post("/api/projects", json={"title": "Number check"}).json()["id"]


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


def _stat(run_id, path):
    return {"type": "stat", "attrs": {"analysis_run_id": run_id, "path": path}}


def _write_version_directly(pid, did, content, client_save_id):
    """Writes a version without going through the save API's integrity check, so an invalid number can be read back."""
    with SessionLocal() as db:
        db.add(DocumentVersion(document_id=uuid.UUID(did), project_id=uuid.UUID(pid), number=1, content_json=content,
                               content_text="", content_hash="0" * 64, saved_by="user:x", client_save_id=client_save_id))
        db.commit()


def _check(client, pid, did):
    response = client.get(f"/api/projects/{pid}/documents/{did}/link-check")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_number_that_resolves_against_a_known_run_has_no_findings():
    client, _ = _login("num.ok@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    run = _run(pid, OUTPUT)
    response = client.post(
        f"/api/projects/{pid}/documents/{intro}/versions",
        json={"base_number": 0, "client_save_id": "x", "client_session_id": "s",
              "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(run, "descriptives.age.mean")]}]}},
    )
    assert response.status_code == 201, response.text

    report = _check(client, pid, intro)

    assert report["findings"] == []


def test_a_number_whose_path_does_not_resolve_is_flagged():
    client, _ = _login("num.unresolved@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    run = _run(pid, OUTPUT)
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(run, "descriptives.age.missing")]}]}
    _write_version_directly(pid, intro, content, "unresolved")  # the save API refuses this, so write it directly

    report = _check(client, pid, intro)

    assert [f["kind"] for f in report["findings"]] == ["unresolved_number"]
    assert report["findings"][0]["subject"] == f"{run}:descriptives.age.missing"


def test_a_number_naming_a_run_from_another_project_is_flagged_as_unknown():
    client, _ = _login("num.foreign@example.org")
    pid = _project(client)
    other_client, _ = _login("num.other@example.org")
    other_pid = _project(other_client)
    foreign_run = _run(other_pid, OUTPUT)
    intro = _section(client, pid, "Introduction", 0)
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(foreign_run, "descriptives.age.mean")]}]}
    _write_version_directly(pid, intro, content, "foreign")  # the save API refuses this, so write it directly

    report = _check(client, pid, intro)

    assert [f["kind"] for f in report["findings"]] == ["unknown_run"]
    assert report["findings"][0]["subject"] == foreign_run


def test_the_save_api_refuses_an_unresolved_number():
    client, _ = _login("num.save.refuse@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    run = _run(pid, OUTPUT)

    response = client.post(
        f"/api/projects/{pid}/documents/{intro}/versions",
        json={"base_number": 0, "client_save_id": "x", "client_session_id": "s",
              "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(run, "nope")]}]}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unresolved_number"
