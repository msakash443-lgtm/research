"""Inline dev mode (RUN_RESEARCH_INLINE=true, the test default): the request runs its own task through the queue."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_queue as q
from app import task_runner
from app.agent import executor
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, ResearchRun, Task, TaskStatus, User


def _client_and_project(email):
    assert get_settings().run_research_inline is True  # the suite's default
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "I"})
    project_id = client.post("/api/projects", json={"title": "Inline"}).json()["id"]
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "Participation rose."}).json()["id"]
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")  # cited sources must be verified (M1.10.3)
    return client, project_id


def _run(client, project_id):
    return client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"})


def test_an_inline_run_finishes_in_the_request_through_a_completed_task(fake_llm):
    client, project_id = _client_and_project("inline-ok@example.com")

    response = _run(client, project_id)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed" and body["answer"] == "Fake answer [S1]." and body["provider_model"] == "fake-model"
    with SessionLocal() as db:
        task = db.scalars(select(Task)).one()
    assert task.type == "research_run" and task.status == TaskStatus.completed and task.payload == {"run_id": body["id"]}
    assert task.idempotency_key == f"research_run:{body['id']}" and task.attempts == 1


def test_an_inline_run_that_cannot_proceed_still_returns_normally():
    client, project_id = _client_and_project("inline-config@example.com")  # no LLM configured

    body = _run(client, project_id).json()

    assert body["status"] == "needs_configuration" and "LLM_API_KEY" in body["error_message"]


def test_an_unexpected_executor_error_is_a_failed_run_not_a_500(fake_llm, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom inside the executor")

    monkeypatch.setattr(executor, "build_plan", explode)
    client, project_id = _client_and_project("inline-crash@example.com")

    response = _run(client, project_id)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed" and "unexpectedly" in body["error_message"] and body["answer"] is None
    with SessionLocal() as db:
        task = db.scalars(select(Task)).one()
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id)))]
    # Retrying can't fix it, so the task fails at once instead of looping.
    assert task.status == TaskStatus.failed and task.attempts == 1
    assert actions.count("task.failed") == 1 and "research_run.failed" in actions


def test_an_inline_request_runs_only_its_own_task_not_someone_elses(fake_llm):
    with SessionLocal() as db:
        owner = User(email=f"inline-other-{uuid.uuid4().hex}@example.test")
        db.add(owner)
        db.flush()
        other = Project(owner_id=owner.id, title="Other")
        db.add(other)
        db.flush()
        waiting = q.enqueue_task(db, other.id, "research_run", {"run_id": str(uuid.uuid4())})
        waiting.created_at = datetime.now(timezone.utc) - timedelta(hours=1)  # clearly older: a plain claim would take it
        db.commit()
        waiting_id = waiting.id
    client, project_id = _client_and_project("inline-own@example.com")

    body = _run(client, project_id).json()

    assert body["status"] == "completed"
    with SessionLocal() as db:
        assert db.get(Task, waiting_id).status == TaskStatus.queued  # untouched
        assert db.scalars(select(ResearchRun).where(ResearchRun.id == uuid.UUID(body["id"]))).one().status.value == "completed"


def test_run_one_task_with_an_id_only_takes_that_task_and_only_if_it_is_due():
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        owner = User(email=f"inline-id-{uuid.uuid4().hex}@example.test")
        db.add(owner)
        db.flush()
        project = Project(owner_id=owner.id, title="ById")
        db.add(project)
        db.flush()
        paused = Task(project_id=project.id, type="research_run", status=TaskStatus.paused)
        later = Task(project_id=project.id, type="research_run", run_after=now + timedelta(hours=1))
        db.add_all([paused, later])
        db.commit()
        paused_id, later_id = paused.id, later.id

    assert task_runner.run_one_task(now, task_id=paused_id) is False
    assert task_runner.run_one_task(now, task_id=later_id) is False
    assert q.claim_next_task(now, task_id=uuid.uuid4()) is None
    with SessionLocal() as db:
        assert db.get(Task, paused_id).status == TaskStatus.paused and db.get(Task, later_id).status == TaskStatus.queued
