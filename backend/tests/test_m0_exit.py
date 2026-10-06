"""M0 exit check (plan M0.11.1, spec milestone M0): create a project, share it, log events, run a gated task.

One story, told through the public API and the worker, so it fails if any of the milestone's pieces
(roles and sharing, the audit log, gates, the task queue) stops working with the others:

  1. an owner creates a project and shares it with a co-author, a supervisor and a reviewer;
  2. the co-author contributes; the reviewer can read but not write; an outsider sees nothing;
  3. research runs through the durable queue, not inside the web request;
  4. a task that needs a human gate is created *blocked* and the worker will not touch it;
  5. a co-author and a reviewer cannot approve the gate, a rejection leaves it blocked, and only the
     supervisor's approval releases it, after which the worker runs it exactly once;
  6. every step above is in the audit trail with the right person against it, readable and exportable.
"""

import csv
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import task_queue as q
from app import task_registry as registry
from app import task_runner
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, GateCode, Task, TaskStatus


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": email.split("@")[0]}).json()["id"]
    return client, user_id


def _trail(project_id):
    """The project's audit events in the order they were written."""
    with SessionLocal() as db:
        rows = db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id)).order_by(text("rowid"))
        ).all()
        return [(e.action, e.actor, e.payload_json) for e in rows]


def _is_subsequence(wanted, trail_actions):
    it = iter(trail_actions)
    return all(any(item == candidate for candidate in it) for item in wanted)


@pytest.fixture
def bulk_search():
    """A task type that must wait for G2 (search strategy approved), like the real bulk search will."""
    calls = []
    registry.register("bulk_search", requires_gate=GateCode.G2)(lambda task: calls.append(task.id))
    yield calls
    registry.HANDLERS.pop("bulk_search", None)
    registry.REQUIRED_GATES.pop("bulk_search", None)


def test_m0_exit_create_share_log_and_run_a_gated_task(fake_llm, bulk_search, monkeypatch):
    monkeypatch.setattr(get_settings(), "run_research_inline", False)  # the worker, not the request, does the work

    # 1. create and share --------------------------------------------------------------------------------
    owner, owner_id = _login("m0-owner@example.com")
    coauthor, coauthor_id = _login("m0-coauthor@example.com")
    supervisor, supervisor_id = _login("m0-supervisor@example.com")
    reviewer, reviewer_id = _login("m0-reviewer@example.com")
    outsider, _ = _login("m0-outsider@example.com")

    project_id = owner.post("/api/projects", json={"title": "Female labour participation"}).json()["id"]
    for email, role in (("m0-coauthor@example.com", "co_author"), ("m0-supervisor@example.com", "supervisor"), ("m0-reviewer@example.com", "reviewer")):
        assert owner.post(f"/api/projects/{project_id}/members", json={"email": email, "role": role}).status_code == 201

    # 2. the team works within its roles; an outsider sees nothing ---------------------------------------
    assert coauthor.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why the regional gap?"}).status_code == 201
    added = coauthor.post(f"/api/projects/{project_id}/sources", json={"title": "Official release", "evidence_excerpt": "Participation rose."})
    assert added.status_code == 201
    # A co-author checks the source's details; only verified sources may be cited in an answer (M1.10.3).
    assert coauthor.post(f"/api/projects/{project_id}/sources/{added.json()['id']}/verify").status_code == 200
    assert reviewer.post(f"/api/projects/{project_id}/sources", json={"title": "Not allowed"}).status_code == 403
    for member in (owner, coauthor, supervisor, reviewer):
        assert project_id in [p["id"] for p in member.get("/api/projects").json()]
    assert outsider.get(f"/api/projects/{project_id}").status_code == 404 and outsider.get("/api/projects").json() == []

    # 3. research runs through the durable queue --------------------------------------------------------
    queued = coauthor.post(f"/api/projects/{project_id}/research-runs", json={"question": "What does the evidence say?"})
    assert queued.status_code == 202 and queued.json()["status"] == "queued" and fake_llm.requests == []
    assert task_runner.run_one_task() is True  # the worker picks it up
    run = coauthor.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["status"] == "completed" and run["answer"] and run["created_by"] == coauthor_id
    assert run["provider_model"] == "fake-model" and run["prompt_version"].startswith("evidence_synthesis@")

    # 4. a gated task is born blocked and the worker leaves it alone ----------------------------------
    with SessionLocal() as db:
        gated = q.enqueue_task(db, uuid.UUID(project_id), "bulk_search", {"query": "labour AND gender"}, actor=owner_id)
        db.commit()
        gated_id = gated.id
        assert (gated.status, gated.blocked_by_gate) == (TaskStatus.blocked, GateCode.G2)
    assert task_runner.run_one_task() is False and bulk_search == []

    # 5. only a supervisor's approval releases it -------------------------------------------------------
    approve = lambda who: who.post(f"/api/projects/{project_id}/gates/G2/approve", json={"note": "Strategy is sound"})  # noqa: E731
    assert approve(coauthor).status_code == 403 and approve(reviewer).status_code == 403
    assert task_runner.run_one_task() is False
    rejected = supervisor.post(f"/api/projects/{project_id}/gates/G2/reject", json={"note": "Add the grey literature"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert task_runner.run_one_task() is False and bulk_search == []  # a rejection keeps it blocked

    assert approve(supervisor).status_code == 200
    with SessionLocal() as db:
        assert db.get(Task, gated_id).status == TaskStatus.queued  # released by the approval, not by a timer
    assert task_runner.run_one_task() is True
    assert bulk_search == [gated_id]  # ran exactly once
    with SessionLocal() as db:
        assert db.get(Task, gated_id).status == TaskStatus.completed
    assert task_runner.run_one_task() is False and bulk_search == [gated_id]

    # 6. the audit trail tells the same story, with the right people ----------------------------------
    trail = _trail(project_id)
    story = [
        ("project.created", owner_id), ("member.invited", owner_id), ("member.invited", owner_id), ("member.invited", owner_id),
        ("context.added", coauthor_id), ("source.created", coauthor_id), ("research_run.queued", coauthor_id),
        ("research_run.finished", "agent:research-run"), ("task.blocked", owner_id),
        ("gate.rejected", supervisor_id), ("gate.approved", supervisor_id), ("task.released", supervisor_id),
    ]
    assert _is_subsequence(story, [(action, actor) for action, actor, _ in trail]), trail
    approved = next(payload for action, _actor, payload in trail if action == "gate.approved")
    assert approved == {"gate": "G2", "role": "supervisor", "note": "Strategy is sound"}
    released = next(payload for action, _actor, payload in trail if action == "task.released")
    assert released["gate"] == "G2" and released["task_ids"] == [str(gated_id)]
    assert not any(action == "gate.approved" and actor in {coauthor_id, reviewer_id} for action, actor, _ in trail)

    # ...and it is readable by the team and exportable --------------------------------------------------
    listed = supervisor.get(f"/api/projects/{project_id}/audit", params={"limit": 1000}).json()
    assert {e["action"] for e in listed} >= {action for action, _ in story}
    exported = list(csv.DictReader(io.StringIO(reviewer.get(f"/api/projects/{project_id}/audit/export", params={"format": "csv"}).text)))
    assert len(exported) == len(trail) and {"timestamp", "actor", "action"} <= set(exported[0])
    assert outsider.get(f"/api/projects/{project_id}/audit").status_code == 404


def test_without_an_approval_the_gated_task_never_runs_however_often_the_worker_looks(bulk_search):
    owner, _ = _login("m0-idle-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "Waiting"}).json()["id"]
    with SessionLocal() as db:
        q.enqueue_task(db, uuid.UUID(project_id), "bulk_search")
        db.commit()

    from app import worker

    for _ in range(5):
        assert worker.release_gated_tasks() == 0  # the worker's own sweep releases only approved gates
        assert task_runner.run_one_task() is False

    assert bulk_search == []
    with SessionLocal() as db:
        assert db.scalars(select(Task)).one().status == TaskStatus.blocked
