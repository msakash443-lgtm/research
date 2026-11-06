"""Plan M1.12: saved-query alerts. Offline only (a fake connector; no network).

A scheduled re-run re-runs the stored search as its next version and surfaces the records that
appear in no earlier version of that search. Re-runs are G2-gated tasks, like search runs.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_runner
from gate_helpers import approve_earlier_gates
from app.config import get_settings
from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage, SearchRequest
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, SearchAlert, SearchQuery, Task, TaskStatus
from app.query_alerts import new_records, sweep_due_alerts

BODY = {
    "database": "openalex",
    "blocks": [{"label": "a", "terms": ["remote work"]}, {"label": "b", "terms": ["productivity"]}],
}


def paper(i, doi=None):
    return PaperRecord(
        connector="openalex", external_id=f"W{i}",
        title=f"A distinct and long enough title {i} on work", doi=doi, year=2020,
    )


class FakeConnector(ConnectorBase):
    name = "openalex"
    fail = False
    records = (paper(0, "10.1000/p0"), paper(1, "10.1000/p1"), paper(2, "10.1000/p2"))

    def search(self, request: SearchRequest) -> SearchPage:
        if FakeConnector.fail:
            raise ConnectorError("upstream down")
        return SearchPage(records=FakeConnector.records, total=len(FakeConnector.records), next_cursor=None)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])
    monkeypatch.setattr("app.task_handlers.build_connector", lambda name: FakeConnector())
    FakeConnector.fail = False
    FakeConnector.records = (paper(0, "10.1000/p0"), paper(1, "10.1000/p1"), paper(2, "10.1000/p2"))


def _login(email):
    client = TestClient(app)
    uid = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, uid


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    client, owner_id = _login(f"qa-owner-{tag}@example.com")
    pid = client.post("/api/projects", json={"title": "Alerts"}).json()["id"]
    return {"client": client, "owner_id": owner_id, "pid": pid}


def run_all():
    while task_runner.run_one_task():
        pass


def run_search_v1(world):
    """Approve G2 (a person decision) and run the first version."""
    approve_earlier_gates(world["pid"], "G2")
    assert world["client"].post(f"/api/projects/{world['pid']}/gates/G2/approve", json={"note": "ok"}).status_code == 200
    resp = world["client"].post(f"/api/projects/{world['pid']}/searches", json=BODY)
    assert resp.status_code == 202, resp.text
    run_all()
    return resp.json()["search_id"]


def versions(pid):
    with SessionLocal() as db:
        rows = db.scalars(select(SearchQuery).where(
            SearchQuery.project_id == uuid.UUID(pid)).order_by(SearchQuery.version)).all()
        db.expunge_all()
        return rows


def alert_row(pid):
    with SessionLocal() as db:
        alert = db.scalar(select(SearchAlert).where(SearchAlert.project_id == uuid.UUID(pid)))
        if alert:
            db.expunge(alert)
        return alert


def make_alert_due(world, *, past_seconds=3600):
    """Subscribe (G2 already approved by run_search_v1) and put the alert in the past."""
    search_id = versions(world["pid"])[0].search_id
    resp = world["client"].post(f"/api/projects/{world['pid']}/searches/{search_id}/alerts")
    assert resp.status_code == 201, resp.text
    with SessionLocal() as db:
        db.get(SearchAlert, uuid.UUID(resp.json()["alert_id"])).next_run_at = datetime.now(timezone.utc) - timedelta(
            seconds=past_seconds)
        db.commit()


def subscribe_and_run(world, change=None):
    """Sweep the due alert and run the task it enqueues (G2 was approved in setup)."""
    if change is not None:
        FakeConnector.records = change
    with SessionLocal() as db:
        n = sweep_due_alerts(db)
        db.commit()
    assert n == 1
    run_all()


# --- subscription -------------------------------------------------------------


def test_subscribe_creates_an_alert_with_default_interval(world):
    search_id = run_search_v1(world)

    resp = world["client"].post(f"/api/projects/{world['pid']}/searches/{search_id}/alerts")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["search_id"] == search_id and body["interval_seconds"] == 86400
    assert body["enabled"] is True and body["last_run_at"] is None and body["last_new"] is None
    next_run = datetime.fromisoformat(body["next_run_at"])
    assert timedelta(hours=23) < next_run - datetime.now(timezone.utc) < timedelta(hours=25)
    alert = alert_row(world["pid"])
    assert alert.created_by == world["owner_id"]


def test_subscribe_accepts_a_custom_interval(world):
    search_id = run_search_v1(world)

    resp = world["client"].post(
        f"/api/projects/{world['pid']}/searches/{search_id}/alerts", json={"interval_seconds": 7 * 86400}
    )

    assert resp.status_code == 201 and resp.json()["interval_seconds"] == 7 * 86400


def test_subscribe_refusals(world):
    search_id = run_search_v1(world)
    base = f"/api/projects/{world['pid']}/searches"

    unknown = world["client"].post(f"{base}/{uuid.uuid4()}/alerts")
    assert unknown.status_code == 404
    short = world["client"].post(f"{base}/{search_id}/alerts", json={"interval_seconds": 60})
    long = world["client"].post(f"{base}/{search_id}/alerts", json={"interval_seconds": 31 * 86400})
    assert short.status_code == 422 and long.status_code == 422

    # A search that has never run cannot be watched: there is nothing to re-run.
    with SessionLocal() as db:
        unrun_id = uuid.uuid4()
        db.add(SearchQuery(project_id=uuid.UUID(world["pid"]), search_id=unrun_id,
                           database="openalex", query_string='"x"'))
        db.commit()
    unrun = world["client"].post(f"{base}/{unrun_id}/alerts")
    assert unrun.status_code == 409 and "not been run" in unrun.json()["detail"]

    first = world["client"].post(f"{base}/{search_id}/alerts")
    second = world["client"].post(f"{base}/{search_id}/alerts")
    assert first.status_code == 201 and second.status_code == 409 and "already" in second.json()["detail"]


# --- scheduling ---------------------------------------------------------------


def test_sweep_enqueues_each_due_alert_once_per_slot(world):
    search_id = run_search_v1(world)
    world["client"].post(f"/api/projects/{world['pid']}/searches/{search_id}/alerts")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.get(SearchAlert, alert_row(world["pid"]).id).next_run_at = now - timedelta(hours=1)
        db.commit()
        n = sweep_due_alerts(db, now=now)
        db.commit()

    assert n == 1
    # the /searches pending list only holds search_run tasks, so check the DB
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.project_id == uuid.UUID(world["pid"])).where(Task.type == "query_alert"))
        db.expunge(task)
    # G2 is approved here, so the task is queued, not blocked
    assert task.status == TaskStatus.queued
    # the same sweep (same clock) cannot enqueue the same slot twice
    with SessionLocal() as db:
        db.get(SearchAlert, alert_row(world["pid"]).id).next_run_at = now - timedelta(hours=1)
        db.commit()
        n2 = sweep_due_alerts(db, now=now)
        db.commit()
    assert n2 == 1  # claimed, but the idempotency key returns the same task
    with SessionLocal() as db:
        count = len(db.scalars(select(Task).where(Task.project_id == uuid.UUID(world["pid"])).where(Task.type == "query_alert")).all())
    assert count == 1
    # the alert advanced to its next slot
    assert alert_row(world["pid"]).next_run_at > now.replace(tzinfo=None)  # SQLite reads back naive


def test_sweep_skips_disabled_alerts(world):
    run_search_v1(world)
    make_alert_due(world)
    with SessionLocal() as db:
        db.get(SearchAlert, alert_row(world["pid"]).id).enabled = False
        db.commit()
        n = sweep_due_alerts(db)
        db.commit()
    assert n == 0


# --- running: new-paper semantics ----------------------------------------------


def test_alert_run_surfaces_only_papers_new_since_earlier_versions(world):
    run_search_v1(world)  # v1: W0, W1, W2
    make_alert_due(world)

    # v2: the database now also returns W3; W0 dropped out.
    subscribe_and_run(world, change=(paper(1, "10.1000/p1"), paper(2, "10.1000/p2"), paper(3, "10.1000/p3")))

    rows = versions(world["pid"])
    assert [r.version for r in rows] == [1, 2]
    listed = world["client"].get(f"/api/projects/{world['pid']}/alerts").json()
    assert listed[0]["last_run_version"] == 2 and listed[0]["last_new"] == 1
    assert listed[0]["last_new_results"] == [{"id": "openalex:W3", "doi": "10.1000/p3"}]
    assert listed[0]["latest_version"] == 2

    with SessionLocal() as db:
        events = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world['pid']))).all()}
    assert {"query_alert.created", "query_alert.checked", "query_alert.new"} <= events
    with SessionLocal() as db:
        new_event = db.scalar(select(AuditEvent).where(
            AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action == "query_alert.new"))
        db.expunge(new_event)
    assert new_event.payload_json["new"] == [{"id": "openalex:W3", "doi": "10.1000/p3"}]
    assert alert_row(world["pid"]).last_new == 1


def test_a_paper_that_left_and_came_back_is_not_new(world):
    run_search_v1(world)  # v1: W0, W1, W2
    make_alert_due(world)
    subscribe_and_run(world, change=(paper(1, "10.1000/p1"),))          # v2: W1 only
    # v3: W0 comes back — it was surfaced in v1, so it is not "new" again.
    with SessionLocal() as db:
        db.get(SearchAlert, alert_row(world["pid"]).id).next_run_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    subscribe_and_run(world, change=(paper(0, "10.1000/p0"), paper(1, "10.1000/p1")))

    rows = versions(world["pid"])
    assert [r.version for r in rows] == [1, 2, 3]
    assert alert_row(world["pid"]).last_new == 0
    listed = world["client"].get(f"/api/projects/{world['pid']}/alerts").json()
    assert listed[0]["last_new_results"] == []


def test_a_rerun_with_nothing_new_reports_zero(world):
    run_search_v1(world)
    make_alert_due(world)
    subscribe_and_run(world)  # same records as v1

    assert alert_row(world["pid"]).last_new == 0
    assert world["client"].get(f"/api/projects/{world['pid']}/alerts").json()[0]["last_new_results"] == []


# --- the gate -------------------------------------------------------------------


def test_alert_runs_are_blocked_until_a_person_approves_g2(world):
    # G1/G2 still pending (no run_search_v1): create the v1 search + alert directly.
    approve_earlier_gates(world["pid"], "G1")
    with SessionLocal() as db:
        project_id = uuid.UUID(world["pid"])
        search_id = uuid.uuid4()
        db.add(SearchQuery(project_id=project_id, search_id=search_id, database="openalex",
                           query_string='"x"', run_at=datetime.now(timezone.utc), n_results=3,
                           results=[{"id": "W0", "doi": "10.1000/p0", "work_key": f"t0"},
                                    {"id": "W1", "doi": "10.1000/p1", "work_key": "t1"},
                                    {"id": "W2", "doi": "10.1000/p2", "work_key": "t2"}]))
        alert_id = uuid.uuid4()
        db.add(SearchAlert(id=alert_id, project_id=project_id, search_id=search_id, interval_seconds=86400,
                           next_run_at=datetime.now(timezone.utc) - timedelta(hours=1), enabled=True))
        db.commit()

    with SessionLocal() as db:
        n = sweep_due_alerts(db)
        db.commit()
    assert n == 1
    with SessionLocal() as db:
        task_id = db.scalar(select(Task).where(Task.project_id == uuid.UUID(world["pid"])).where(Task.type == "query_alert")).id
    assert task_runner.run_one_task() is False  # G2 pending: nothing runs
    assert len(versions(world["pid"])) == 1

    approve_earlier_gates(world["pid"], "G2")
    assert world["client"].post(f"/api/projects/{world['pid']}/gates/G2/approve", json={"note": "ok"}).status_code == 200
    run_all()
    assert len(versions(world["pid"])) == 2
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.status == TaskStatus.completed


# --- failure and crash paths ------------------------------------------------------


def test_failed_run_leaves_no_state_and_eventually_fails(world):
    run_search_v1(world)
    make_alert_due(world)
    FakeConnector.fail = True
    with SessionLocal() as db:
        n = sweep_due_alerts(db)
        db.commit()
    assert n == 1
    # exhaust the retries so the failure is terminal, not a backoff
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.project_id == uuid.UUID(world["pid"])).where(Task.type == "query_alert"))
        task.attempts = task.max_attempts - 1
        db.commit()
    run_all()

    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.project_id == uuid.UUID(world["pid"])).where(Task.type == "query_alert"))
        db.expunge(task)
    assert task.status == TaskStatus.failed and "401" not in (task.error or "") and task.error
    assert len(versions(world["pid"])) == 1  # no partial run saved
    assert alert_row(world["pid"]).last_run_at is None
    with SessionLocal() as db:
        events = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world['pid']))).all()}
    assert "search.failed" in events and "query_alert.checked" not in events


def test_a_retried_run_after_a_crash_is_a_noop(world):
    run_search_v1(world)
    make_alert_due(world)
    with SessionLocal() as db:
        enqueued = sweep_due_alerts(db)
        db.commit()
    assert enqueued == 1
    run_all()  # v2 completes; the alert's last_run_at is set
    assert alert_row(world["pid"]).last_run_version == 2

    # The worker died after the commit but before acknowledging the task: the queue hands it back.
    from app import task_queue as q

    with SessionLocal() as db:
        original = db.scalar(select(Task).where(Task.project_id == uuid.UUID(world["pid"])).where(Task.type == "query_alert"))
        db.expunge(original)
        q.enqueue_task(db, uuid.UUID(world["pid"]), "query_alert", dict(original.payload))
        db.commit()
    run_all()

    assert len(versions(world["pid"])) == 2  # no v3
    with SessionLocal() as db:
        checked = db.scalars(select(AuditEvent).where(
            AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action == "query_alert.checked")).all()
        db.expunge_all()
    assert len(checked) == 1


# --- manual run and delete ---------------------------------------------------------


def test_manual_run_queues_the_same_gated_task(world):
    run_search_v1(world)
    make_alert_due(world)
    alert_id = alert_row(world["pid"]).id

    first = world["client"].post(f"/api/projects/{world['pid']}/alerts/{alert_id}/run")
    assert first.status_code == 202 and first.json()["status"] == "queued"
    blocked = world["client"].post(f"/api/projects/{world['pid']}/alerts/{alert_id}/run")
    assert blocked.status_code == 409  # one run at a time per alert
    run_all()
    assert alert_row(world["pid"]).last_run_version == 2

    again = world["client"].post(f"/api/projects/{world['pid']}/alerts/{alert_id}/run")
    assert again.status_code == 202  # the previous run finished, so a new one may queue
    assert world["client"].post(
        f"/api/projects/{world['pid']}/alerts/{uuid.uuid4()}/run").status_code == 404


def test_delete_removes_the_subscription(world):
    run_search_v1(world)
    make_alert_due(world)
    alert_id = alert_row(world["pid"]).id

    resp = world["client"].delete(f"/api/projects/{world['pid']}/alerts/{alert_id}")

    assert resp.status_code == 204
    assert world["client"].get(f"/api/projects/{world['pid']}/alerts").json() == []
    assert world["client"].delete(f"/api/projects/{world['pid']}/alerts/{alert_id}").status_code == 404
    with SessionLocal() as db:
        events = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world['pid']))).all()}
    assert "query_alert.deleted" in events
