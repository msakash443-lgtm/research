"""Plan M1.8.1: bulk retrieval is blocked until a person approves gate G2."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_handlers, task_runner
from gate_helpers import approve_earlier_gates
from app.config import get_settings
from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage, SearchRequest
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, ProjectMember, ProjectRole, SearchQuery, Task, TaskStatus

BODY = {"database": "openalex", "blocks": [{"label": "a", "terms": ["remote work", "telework"]}, {"label": "b", "terms": ["productivity"]}]}


class FakeConnector(ConnectorBase):
    name = "openalex"
    fail = False
    calls = 0

    def search(self, request: SearchRequest) -> SearchPage:
        FakeConnector.calls += 1
        if FakeConnector.fail:
            raise ConnectorError("upstream down")
        recs = tuple(
            PaperRecord(connector="openalex", external_id=f"W{i}", title=f"A distinct and long enough title {i} on work", year=2020)
            for i in range(3)
        )
        return SearchPage(records=recs, total=3, next_cursor=None)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])
    monkeypatch.setattr(task_handlers, "build_connector", lambda name: FakeConnector())
    FakeConnector.fail = False
    FakeConnector.calls = 0


def _login(email):
    client = TestClient(app)
    uid = client.post("/api/auth/development/login", json={"email": email, "display_name": "S"}).json()["id"]
    return client, uid


@pytest.fixture
def team():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"sg-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Search gate"}).json()["id"]
    co, co_id = _login(f"sg-co-{tag}@example.com")
    sup, sup_id = _login(f"sg-sup-{tag}@example.com")
    rev, rev_id = _login(f"sg-rev-{tag}@example.com")
    with SessionLocal() as db:
        for uid, role in ((co_id, ProjectRole.co_author), (sup_id, ProjectRole.supervisor), (rev_id, ProjectRole.reviewer)):
            db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(uid), role=role))
        db.commit()
    approve_earlier_gates(pid, "G2")  # G1; gates go in order (M0.5.10)
    return {"owner": owner, "owner_id": owner_id, "co": co, "sup": sup, "rev": rev, "pid": pid}


def queue(t, client="owner", **over):
    return t[client].post(f"/api/projects/{t['pid']}/searches", json={**BODY, **over})


def rows(pid):
    with SessionLocal() as db:
        return db.scalars(select(SearchQuery).where(SearchQuery.project_id == uuid.UUID(pid)).order_by(SearchQuery.version)).all()


def run_all():
    while task_runner.run_one_task():
        pass


def actions(t):
    return [e["action"] for e in t["owner"].get(f"/api/projects/{t['pid']}/audit").json()]


def approve_g2(t, who="sup"):
    return t[who].post(f"/api/projects/{t['pid']}/gates/G2/approve")


def test_a_queued_search_is_blocked_by_g2_and_the_worker_never_runs_it(team):
    r = queue(team)
    assert r.status_code == 202
    assert r.json()["status"] == "blocked" and r.json()["blocked_by_gate"] == "G2" and r.json()["version"] == 1

    for _ in range(3):
        run_all()
        task_runner.run_one_task()
    assert FakeConnector.calls == 0 and rows(team["pid"]) == []
    listing = team["owner"].get(f"/api/projects/{team['pid']}/searches").json()
    assert listing["searches"] == [] and listing["pending"][0]["status"] == "blocked"


def test_rejection_and_non_approvers_leave_it_blocked(team):
    queue(team)
    assert approve_g2(team, "co").status_code == 403
    assert approve_g2(team, "rev").status_code == 403
    rej = team["sup"].post(f"/api/projects/{team['pid']}/gates/G2/reject", json={"note": "Query too narrow"})
    assert rej.status_code == 200
    run_all()
    assert FakeConnector.calls == 0 and rows(team["pid"]) == []


def test_approval_releases_the_search_which_then_runs_once_and_is_attributed(team):
    queued = queue(team).json()
    assert approve_g2(team).status_code == 200
    run_all()
    run_all()

    (row,) = rows(team["pid"])
    assert str(row.search_id) == queued["search_id"] and row.version == 1 and row.n_results == 3
    assert row.created_by == team["owner_id"]  # the person who asked, not the approver
    assert row.query_string == '("remote work" OR "telework") AND ("productivity")'
    assert FakeConnector.calls == 1
    assert {"search.requested", "task.blocked", "gate.approved", "task.released", "search.run"} <= set(actions(team))
    listing = team["owner"].get(f"/api/projects/{team['pid']}/searches").json()
    assert listing["pending"] == [] and listing["searches"][0]["versions"][0]["n_results"] == 3


def test_a_search_queued_after_approval_is_not_blocked(team):
    approve_g2(team)
    assert queue(team).json()["status"] == "queued"
    run_all()
    assert len(rows(team["pid"])) == 1


def test_a_crash_after_saving_does_not_run_the_search_twice(team):
    approve_g2(team)
    queue(team)
    run_all()
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.type == "search_run", Task.project_id == uuid.UUID(team["pid"])))
        payload = task.payload
    claimed = type("T", (), {"payload": payload, "attempt": 2, "ensure_owned": lambda self, db=None: None})()
    task_handlers.handle_search_run(claimed)  # the retry of a task whose first attempt had saved its run
    assert len(rows(team["pid"])) == 1 and FakeConnector.calls == 1


def test_a_failing_connector_saves_nothing_logs_it_and_the_task_ends_failed(team):
    approve_g2(team)
    FakeConnector.fail = True
    queue(team)
    base = datetime.now(timezone.utc)
    for n in range(6):
        task_runner.run_one_task(now=base + timedelta(hours=n + 1))
    assert rows(team["pid"]) == []
    assert actions(team).count("search.failed") >= 2
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.type == "search_run", Task.project_id == uuid.UUID(team["pid"])))
        assert task.status == TaskStatus.failed


def test_rerun_is_the_next_version_and_cannot_be_stacked(team):
    approve_g2(team)
    first = queue(team).json()
    run_all()
    rerun = team["owner"].post(f"/api/projects/{team['pid']}/searches/{first['search_id']}/rerun")
    assert rerun.status_code == 202 and rerun.json()["version"] == 2 and rerun.json()["status"] == "queued"
    again = team["owner"].post(f"/api/projects/{team['pid']}/searches/{first['search_id']}/rerun")
    assert again.status_code == 409  # one pending run per search
    run_all()
    v1, v2 = rows(team["pid"])
    assert (v1.version, v2.version) == (1, 2) and v2.query_string == v1.query_string
    assert team["owner"].post(f"/api/projects/{team['pid']}/searches/{uuid.uuid4()}/rerun").status_code == 404


def test_a_rerun_is_blocked_again_if_g2_was_voided(team):
    approve_g2(team)
    first = queue(team).json()
    run_all()
    with SessionLocal() as db:  # what re-entry does: the approval no longer covers new work
        from app.models import Gate, GateCode, GateStatus

        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(team["pid"]), Gate.code == GateCode.G2))
        gate.status = GateStatus.pending
        db.commit()
    rerun = team["owner"].post(f"/api/projects/{team['pid']}/searches/{first['search_id']}/rerun").json()
    assert rerun["status"] == "blocked"
    calls = FakeConnector.calls
    run_all()
    assert FakeConnector.calls == calls and len(rows(team["pid"])) == 1


def test_requests_are_validated_before_anything_is_queued(team):
    pid = team["pid"]
    for bad, code in (
        ({"database": "arxiv"}, 400),  # not searchable
        ({"database": "crossref"}, 400),  # not enabled
        ({"blocks": [{"label": "a", "terms": ["AND"]}]}, 422),  # a bare operator word
        ({"blocks": [{"label": "a", "terms": ['say "hi"']}]}, 422),
        ({"blocks": []}, 422),
        ({"max_results": 5000}, 422),
        ({"extra": 1}, 422),
    ):
        assert queue(team, **bad).status_code == code, bad
    assert team["owner"].get(f"/api/projects/{pid}/searches").json()["pending"] == []


def test_roles(team):
    assert queue(team, "rev").status_code == 403
    assert queue(team, "co").status_code == 202
    assert team["rev"].get(f"/api/projects/{team['pid']}/searches").status_code == 200
    stranger, _ = _login(f"sg-x-{uuid.uuid4().hex[:6]}@example.com")
    assert stranger.get(f"/api/projects/{team['pid']}/searches").status_code == 404
    assert TestClient(app).post(f"/api/projects/{team['pid']}/searches", json=BODY).status_code == 401


def test_only_the_gated_handler_runs_searches():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    users = sorted(
        p.name for p in app_dir.rglob("*.py") if p.name != "search_runner.py" and "search_runner" in p.read_text(encoding="utf-8")
    )
    assert users == ["task_handlers.py"]
    assert "requires_gate=GateCode.G2" in (app_dir / "task_handlers.py").read_text(encoding="utf-8")


def test_rerun_of_a_semantic_scholar_search_saved_before_bulk_search_is_refused(team, monkeypatch):
    from app.search_rerun import LEGACY_S2_CAVEAT

    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex", "semantic_scholar"])
    search_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(SearchQuery(project_id=uuid.UUID(team["pid"]), search_id=search_id, database="semantic_scholar",
                           query_string="remote work telework productivity", filters={}, version=1, exact=False,
                           caveats=[LEGACY_S2_CAVEAT + ": ranked by similarity."], counts={"retrieved": 0}, results=[]))
        db.commit()
    r = team["owner"].post(f"/api/projects/{team['pid']}/searches/{search_id}/rerun")
    assert r.status_code == 409 and "Start a new search" in r.json()["detail"]
    with SessionLocal() as db:
        assert not [t for t in db.scalars(select(Task).where(Task.project_id == uuid.UUID(team["pid"])))
                    if (t.payload or {}).get("search_id") == str(search_id)]
