"""Add-your-own-paper by DOI-only/title-only (plan M2.14.1): the queued `own_paper_lookup` task
resolves metadata through the enabled lookup connectors, then includes it the same way
`POST .../sources/own` does. Connectors are faked: no network.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import task_handlers, task_runner
from app.config import get_settings
from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage
from app.main import app
from app.models import GateCode, GateStatus

TITLE = "Remote work and worker productivity: evidence from a field experiment"
AUTHORS = ["Ada Lovelace", "Grace Hopper"]
DOI = "10.1234/rw.2020"


def record(title=TITLE, authors=tuple(AUTHORS), year=2020, doi=DOI):
    return PaperRecord(connector="crossref", external_id="c1", title=title, authors=authors, year=year, doi=doi)


class FakeService(ConnectorBase):
    def __init__(self, name, found=None, raises=None):
        self.name, self.found, self.raises = name, found, raises

    def get_by_id(self, external_id):
        if self.raises:
            raise self.raises
        return self.found

    def search(self, request):
        if self.raises:
            raise self.raises
        return SearchPage(records=(self.found,) if self.found else ())


@pytest.fixture
def services(monkeypatch):
    state = type("S", (), {"found": record(), "raises": None})()
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["crossref"])
    monkeypatch.setattr(task_handlers, "build_connector", lambda name: FakeService(name, state.found, state.raises))
    return state


@pytest.fixture
def world():
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"opl-{uuid.uuid4().hex[:8]}@example.com", "display_name": "O"})
    return c, c.post("/api/projects", json={"title": "Own paper lookup"}).json()["id"]


def run_lookup(c, pid, body):
    queued = c.post(f"/api/projects/{pid}/sources/own/lookup", json=body)
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["task_id"]
    while task_runner.run_one_task():
        pass
    outcome = c.get(f"/api/projects/{pid}/sources/own/lookup", params={"task_id": task_id})
    assert outcome.status_code == 200, outcome.text
    return outcome.json()


def test_a_doi_only_lookup_resolves_through_the_connector_and_includes_the_paper(services, world):
    c, pid = world
    outcome = run_lookup(c, pid, {"doi": DOI})
    assert outcome["status"] == "included"
    assert outcome["matched_connector"] == "crossref"
    assert outcome["source_id"] is not None

    sources = c.get(f"/api/projects/{pid}/sources").json()
    created = next(s for s in sources if s["id"] == outcome["source_id"])
    assert created["title"] == TITLE
    assert created["doi"] == DOI
    assert created["metadata_verified"] is False  # same as a direct POST .../sources/own: unverified, origin=manual

    queue = c.get(f"/api/projects/{pid}/screening/queue").json()
    # the paper is included at both stages, so it no longer appears in the unscreened queue
    assert all(item["source_id"] != outcome["source_id"] for item in queue["items"])


def test_a_title_only_lookup_matches_the_closest_title_above_the_similarity_bar(services, world):
    c, pid = world
    outcome = run_lookup(c, pid, {"title": TITLE})
    assert outcome["status"] == "included"
    assert outcome["source_id"] is not None


def test_a_title_with_no_close_match_is_not_found_and_creates_no_source(services, world):
    c, pid = world
    services.found = record(title="A completely unrelated paper about soil chemistry")
    outcome = run_lookup(c, pid, {"title": TITLE})
    assert outcome["status"] == "not_found"
    assert outcome["source_id"] is None
    assert c.get(f"/api/projects/{pid}/sources").json() == []


def test_a_doi_no_service_knows_is_not_found(services, world):
    c, pid = world
    services.found = None
    outcome = run_lookup(c, pid, {"doi": DOI})
    assert outcome["status"] == "not_found"
    assert outcome["source_id"] is None


def test_a_doi_already_in_the_project_is_refused_before_resolving(services, world):
    c, pid = world
    assert c.post(f"/api/projects/{pid}/sources", json={"title": "Already here", "doi": DOI}).status_code == 201
    queued = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"doi": DOI})
    assert queued.status_code == 409


def test_a_resolved_match_already_in_the_project_fails_the_lookup_not_a_duplicate_source(services, world):
    c, pid = world
    # The dup DOI is only discovered after resolving (added after the lookup was queued).
    other_doi = "10.5555/already-here"
    queued = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"title": TITLE})
    assert queued.status_code == 202
    task_id = queued.json()["task_id"]
    services.found = record(doi=other_doi)
    assert c.post(f"/api/projects/{pid}/sources", json={"title": "Snuck in first", "doi": other_doi}).status_code == 201
    while task_runner.run_one_task():
        pass
    outcome = c.get(f"/api/projects/{pid}/sources/own/lookup", params={"task_id": task_id}).json()
    assert outcome["status"] == "failed"
    assert "already in the project" in outcome["detail"]


def test_screening_locked_refuses_the_request_before_anything_is_queued(services, world):
    c, pid = world
    from app.database import SessionLocal
    from app.models import Gate

    with SessionLocal() as db:
        db.query(Gate).filter(Gate.project_id == uuid.UUID(pid), Gate.code == GateCode.G3).update(
            {"status": GateStatus.approved}
        )
        db.commit()
    refused = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"doi": DOI})
    assert refused.status_code == 409


def test_giving_both_doi_and_title_is_refused(services, world):
    c, pid = world
    refused = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"doi": DOI, "title": TITLE})
    assert refused.status_code == 422


def test_giving_neither_doi_nor_title_is_refused(services, world):
    c, pid = world
    refused = c.post(f"/api/projects/{pid}/sources/own/lookup", json={})
    assert refused.status_code == 422


def test_a_connector_failure_is_retried_not_reported_as_not_found(services, world):
    c, pid = world
    services.raises = ConnectorError("service unavailable")
    queued = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"doi": DOI})
    assert queued.status_code == 202
    task_id = queued.json()["task_id"]

    # One failed attempt: the task is retried (not permanently failed), and no lookup row exists yet.
    assert task_runner.run_one_task() is True
    still_pending = c.get(f"/api/projects/{pid}/sources/own/lookup", params={"task_id": task_id})
    assert still_pending.status_code == 404

    # Once the service recovers, a later attempt resolves it.
    services.raises = None
    from app.database import SessionLocal
    from app.models import Task as TaskModel

    with SessionLocal() as db:
        row = db.query(TaskModel).filter(TaskModel.id == uuid.UUID(task_id)).one()
        row.run_after = None
        db.commit()
    while task_runner.run_one_task():
        pass
    outcome = c.get(f"/api/projects/{pid}/sources/own/lookup", params={"task_id": task_id}).json()
    assert outcome["status"] == "included"


def test_a_lookup_whose_retries_run_out_is_reported_as_failed_not_left_pending_forever(services, world):
    # review 2026-10-20: the task ended `failed` with no lookup row, so the GET said "not resolved yet" forever
    from app.database import SessionLocal
    from app.models import AuditEvent, TaskStatus
    from app.models import Task as TaskModel

    c, pid = world
    services.raises = ConnectorError("service unavailable")
    task_id = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"doi": DOI}).json()["task_id"]
    for _ in range(50):
        with SessionLocal() as db:
            row = db.get(TaskModel, uuid.UUID(task_id))
            if row.status == TaskStatus.failed:
                break
            row.run_after = None
            db.commit()
        task_runner.run_one_task()
    with SessionLocal() as db:
        assert db.get(TaskModel, uuid.UUID(task_id)).status == TaskStatus.failed

    outcome = c.get(f"/api/projects/{pid}/sources/own/lookup", params={"task_id": task_id})
    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["status"] == "failed"
    assert "kept failing" in outcome.json()["detail"]
    assert outcome.json()["source_id"] is None
    with SessionLocal() as db:
        events = db.query(AuditEvent).filter(AuditEvent.action == "source.own_paper_lookup_failed").all()
        assert any(e.payload_json.get("task_id") == task_id and e.payload_json.get("reason") == "retries_exhausted" for e in events)


def test_no_enabled_lookup_connector_fails_the_lookup_permanently(monkeypatch, world):
    c, pid = world
    monkeypatch.setattr(get_settings(), "connectors_enabled", [])
    queued = c.post(f"/api/projects/{pid}/sources/own/lookup", json={"doi": DOI})
    assert queued.status_code == 202
    task_id = queued.json()["task_id"]
    while task_runner.run_one_task():
        pass
    outcome = c.get(f"/api/projects/{pid}/sources/own/lookup", params={"task_id": task_id}).json()
    assert outcome["status"] == "failed"
    assert "no" in outcome["detail"].lower() and "connector" in outcome["detail"].lower()
