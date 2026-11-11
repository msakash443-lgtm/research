"""Link-check of inserted numbers in a manuscript section (M6.3): the route reports a `stat` node that names an
analysis run this project doesn't have, or a path that doesn't resolve to a number in that run's output; it changes
nothing and does not block export (that hard block is `app.manuscript_guard`/export)."""

import uuid

from app.database import SessionLocal
from app.models import DocumentVersion
from tests.api_helpers import create_project, insert_analysis_run, sign_in_user

OUTPUT = {"descriptives": {"age": {"n": 40, "mean": 35.5}}}


def _section(client, pid, title, position):
    response = client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": position})
    assert response.status_code == 201, response.text
    return response.json()["id"]


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
    client, _ = sign_in_user("num.ok@example.org")
    pid = create_project(client, "Number check")
    intro = _section(client, pid, "Introduction", 0)
    run = insert_analysis_run(pid, OUTPUT)
    response = client.post(
        f"/api/projects/{pid}/documents/{intro}/versions",
        json={"base_number": 0, "client_save_id": "x", "client_session_id": "s",
              "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(run, "descriptives.age.mean")]}]}},
    )
    assert response.status_code == 201, response.text

    report = _check(client, pid, intro)

    assert report["findings"] == []


def test_a_number_whose_path_does_not_resolve_is_flagged():
    client, _ = sign_in_user("num.unresolved@example.org")
    pid = create_project(client, "Number check")
    intro = _section(client, pid, "Introduction", 0)
    run = insert_analysis_run(pid, OUTPUT)
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(run, "descriptives.age.missing")]}]}
    _write_version_directly(pid, intro, content, "unresolved")  # the save API refuses this, so write it directly

    report = _check(client, pid, intro)

    assert [f["kind"] for f in report["findings"]] == ["unresolved_number"]
    assert report["findings"][0]["subject"] == f"{run}:descriptives.age.missing"


def test_a_number_naming_a_run_from_another_project_is_flagged_as_unknown():
    client, _ = sign_in_user("num.foreign@example.org")
    pid = create_project(client, "Number check")
    other_client, _ = sign_in_user("num.other@example.org")
    other_pid = create_project(other_client, "Number check")
    foreign_run = insert_analysis_run(other_pid, OUTPUT)
    intro = _section(client, pid, "Introduction", 0)
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(foreign_run, "descriptives.age.mean")]}]}
    _write_version_directly(pid, intro, content, "foreign")  # the save API refuses this, so write it directly

    report = _check(client, pid, intro)

    assert [f["kind"] for f in report["findings"]] == ["unknown_run"]
    assert report["findings"][0]["subject"] == foreign_run


def test_the_save_api_refuses_an_unresolved_number():
    client, _ = sign_in_user("num.save.refuse@example.org")
    pid = create_project(client, "Number check")
    intro = _section(client, pid, "Introduction", 0)
    run = insert_analysis_run(pid, OUTPUT)

    response = client.post(
        f"/api/projects/{pid}/documents/{intro}/versions",
        json={"base_number": 0, "client_save_id": "x", "client_session_id": "s",
              "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [_stat(run, "nope")]}]}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unresolved_number"


def test_the_save_api_stores_the_real_number_whatever_value_the_client_sent():
    client, _ = sign_in_user("num.save.forged@example.org")
    pid = create_project(client, "Number check")
    intro = _section(client, pid, "Introduction", 0)
    run = insert_analysis_run(pid, OUTPUT)
    forged = {**_stat(run, "descriptives.age.mean"), "attrs": {"analysis_run_id": run, "path": "descriptives.age.mean", "value": 99}}

    response = client.post(
        f"/api/projects/{pid}/documents/{intro}/versions",
        json={"base_number": 0, "client_save_id": "forged", "client_session_id": "s",
              "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [forged]}]}},
    )

    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        stored = db.query(DocumentVersion).filter(DocumentVersion.client_save_id == "forged").one()
        stat_node = stored.content_json["content"][0]["content"][0]
    assert stat_node["attrs"]["value"] == 35.5
