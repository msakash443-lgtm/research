import inspect
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_queue as q
from app import task_registry as registry
from app import task_runner, worker
from app.agent import executor
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, ResearchRun, ResearchRunStatus, Task, TaskStatus, User
from app.task_registry import ClaimedTask, PermanentTaskError

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def temp_handlers():
    """Register throwaway handlers and remove them afterwards."""
    added = []

    def add(name, handler, on_failure=None):
        registry.register(name, on_failure=on_failure)(handler)
        added.append(name)

    yield add
    for name in added:
        registry.HANDLERS.pop(name, None)
        registry.FAILURE_HOOKS.pop(name, None)


def _project():
    with SessionLocal() as db:
        user = User(email=f"r-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Runner")
        db.add(project)
        db.commit()
        return project.id


def _task(project_id, type, **fields):
    with SessionLocal() as db:
        task = Task(project_id=project_id, type=type, **fields)
        db.add(task)
        db.commit()
        return task.id


def _only_task():
    with SessionLocal() as db:
        return db.scalars(select(Task)).one()


def _get(model, key):
    with SessionLocal() as db:
        row = db.get(model, key)
        db.expunge(row)
        return row


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---- registry ----------------------------------------------------------------------------

def test_registering_the_same_type_twice_is_an_error(temp_handlers):
    temp_handlers("dup", lambda task: None)

    with pytest.raises(ValueError, match="already has a handler"):
        registry.register("dup")(lambda task: None)


def test_the_research_run_handler_is_registered():
    assert registry.get_handler("research_run") is not None
    assert registry.get_handler("no-such-type") is None


# ---- runner ------------------------------------------------------------------------------

def test_an_empty_queue_returns_false():
    assert task_runner.run_one_task(NOW) is False


def test_a_successful_handler_completes_the_task(temp_handlers):
    seen = []
    temp_handlers("ok", lambda task: seen.append((task.type, task.payload, task.attempt)))
    task_id = _task(_project(), "ok", payload={"a": 1})

    assert task_runner.run_one_task(NOW) is True

    assert seen == [("ok", {"a": 1}, 1)]
    assert _get(Task, task_id).status == TaskStatus.completed


def test_a_failing_handler_is_retried_with_backoff_then_fails(temp_handlers):
    def boom(task):
        raise RuntimeError("kaput")

    temp_handlers("boom", boom)
    task_id = _task(_project(), "boom", max_attempts=2)

    task_runner.run_one_task(NOW)
    first = _get(Task, task_id)
    assert first.status == TaskStatus.queued and first.error == "RuntimeError: kaput"
    assert _utc(first.run_after) == NOW + timedelta(seconds=get_settings().task_retry_base_seconds)

    task_runner.run_one_task(NOW + timedelta(hours=1))
    last = _get(Task, task_id)
    assert last.status == TaskStatus.failed and last.attempts == 2 and last.error == "RuntimeError: kaput"


def test_a_permanent_error_fails_immediately_without_retries(temp_handlers):
    def hopeless(task):
        raise PermanentTaskError("cannot work")

    temp_handlers("hopeless", hopeless)
    task_id = _task(_project(), "hopeless", max_attempts=5)

    task_runner.run_one_task(NOW)

    task = _get(Task, task_id)
    assert task.status == TaskStatus.failed and task.attempts == 1 and task.error == "cannot work"


def test_an_unknown_task_type_fails_loudly_and_is_not_retried():
    task_id = _task(_project(), "mystery")

    task_runner.run_one_task(NOW)

    task = _get(Task, task_id)
    assert task.status == TaskStatus.failed and "mystery" in task.error and task.attempts == 1


def test_failure_hook_runs_once_when_a_task_fails_for_good_not_on_retries(temp_handlers):
    hooked = []

    def boom(task):
        raise RuntimeError("x")

    temp_handlers("hooked", boom, on_failure=hooked.append)
    _task(_project(), "hooked", max_attempts=2)

    task_runner.run_one_task(NOW)
    assert hooked == []
    task_runner.run_one_task(NOW + timedelta(hours=1))
    assert [(t.type, t.attempt) for t in hooked] == [("hooked", 2)]


def test_a_failing_failure_hook_does_not_break_the_queue(temp_handlers):
    def boom(task):
        raise PermanentTaskError("x")

    def bad_hook(task):
        raise RuntimeError("hook exploded")

    temp_handlers("badhook", boom, on_failure=bad_hook)
    task_id = _task(_project(), "badhook")

    assert task_runner.run_one_task(NOW) is True
    assert _get(Task, task_id).status == TaskStatus.failed


def test_the_lease_is_renewed_while_a_handler_runs():
    task_id = _task(_project(), "x")
    claimed = q.claim_next_task()
    before = _utc(_get(Task, task_id).lease_expires_at)
    stop = threading.Event()
    beat = threading.Thread(target=task_runner._heartbeat_loop, args=(claimed, stop, 0.05))
    beat.start()
    time.sleep(1.2)  # SQLite stores whole seconds, so wait long enough for a visible change
    stop.set()
    beat.join(timeout=2)

    assert _utc(_get(Task, task_id).lease_expires_at) > before


def test_the_worker_loop_runs_tasks_and_no_longer_claims_runs_directly():
    source = inspect.getsource(worker.run_forever)
    assert "run_one_task" in source and "reap_expired_tasks" not in source  # the reaper has its own thread (X.115)
    assert "reap_expired_tasks" in inspect.getsource(worker._reaper_loop)
    assert "claim_next_run" not in source and "reap_stale_runs" not in source

def _quiet_worker(monkeypatch):
    # Stub the sweeps and the poll interval so the loop runs without a database.
    for name in ("reap_expired_tasks", "release_gated_tasks", "sweep_due_query_alerts", "sweep_run_snapshot_retention"):
        monkeypatch.setattr(worker, name, lambda: 0)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(research_worker_poll_seconds=0))


def test_a_database_error_in_one_loop_pass_does_not_stop_the_worker(monkeypatch):
    _quiet_worker(monkeypatch)
    stop = threading.Event()
    calls = []

    def flaky_pass():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database connection dropped")
        stop.set()
        return False

    monkeypatch.setattr(worker, "run_one_task", flaky_pass)
    worker.run_forever(stop)

    assert len(calls) == 2  # the error was logged and the loop carried on to the next pass


def test_a_stop_request_ends_the_worker_before_it_claims_more_work(monkeypatch):
    _quiet_worker(monkeypatch)
    stop = threading.Event()
    stop.set()  # e.g. SIGTERM arrived while the previous task was finishing
    monkeypatch.setattr(worker, "run_one_task", lambda: pytest.fail("must not claim new work after a stop request"))

    worker.run_forever(stop)


def test_a_failing_sweep_does_not_stop_queued_tasks_from_running(monkeypatch):
    _quiet_worker(monkeypatch)
    stop = threading.Event()
    sweeps = []
    ran = []

    def broken_sweep():
        sweeps.append(1)
        if len(sweeps) == 3:
            stop.set()
        raise RuntimeError("retention settings unreadable")

    monkeypatch.setattr(worker, "sweep_run_snapshot_retention", broken_sweep)
    monkeypatch.setattr(worker, "run_one_task", lambda: ran.append(1) or False)
    worker.run_forever(stop)

    assert len(ran) == 3  # every pass still ran a task claim, though a sweep failed each time


def test_a_hung_handler_does_not_stop_its_expired_task_being_reaped(temp_handlers, monkeypatch):
    # X.115: one worker whose only handler hangs. The claim loop is stuck inside the handler, so
    # only a reaper on its own thread can act on the lapsed lease; the task must not stay `running`.
    settings = get_settings()
    monkeypatch.setattr(settings, "task_lease_seconds", 1)
    monkeypatch.setattr(settings, "task_max_run_seconds", 1)
    monkeypatch.setattr(settings, "research_worker_poll_seconds", 0.1)
    release = threading.Event()
    hooked = []
    temp_handlers("hangs", lambda task: release.wait(), on_failure=hooked.append)
    task_id = _task(_project(), "hangs", max_attempts=1)
    stop = threading.Event()
    worker_thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
    worker_thread.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            task = _get(Task, task_id)
            if task.attempts > 0 and task.status != TaskStatus.running:  # claimed, then reaped
                break
            time.sleep(0.2)
        assert task.status == TaskStatus.failed, (task.attempts, task.max_attempts, task.error)
        assert "lease expired" in task.error
        assert [info.id for info in hooked] == [task_id]  # the failure hook ran, so the record is not left in progress
    finally:
        release.set()
        stop.set()
        worker_thread.join(timeout=5)



# ---- research runs on the queue ----------------------------------------------------------

def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "R"})
    return client


@pytest.fixture
def queued_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "run_research_inline", False)


def _create_run(client, with_source=True):
    project_id = client.post("/api/projects", json={"title": "Queued runs"}).json()["id"]
    if with_source:
        source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "Participation rose."}).json()["id"]
        client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")  # cited sources must be verified (M1.10.3)
    response = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"})
    return project_id, response


def test_creating_a_run_queues_a_task_and_the_api_shape_is_unchanged(queued_mode, fake_llm):
    client = _login("port-api@example.com")

    project_id, response = _create_run(client)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued" and body["answer"] is None and body["started_at"] is None
    assert {"id", "question", "status", "research_plan", "input_snapshot", "answer", "error_message", "provider_model",
            "use_web_retrieval", "created_by", "created_at", "started_at", "completed_at"} <= set(body)
    with SessionLocal() as db:
        (task,) = db.scalars(select(Task)).all()
    assert (task.type, task.payload, task.status, task.project_id) == (
        "research_run", {"run_id": body["id"]}, TaskStatus.queued, uuid.UUID(project_id)
    )
    assert fake_llm.requests == []  # nothing ran yet


def test_the_worker_runs_the_queued_run_to_completion(queued_mode, fake_llm):
    client = _login("port-run@example.com")
    project_id, created = _create_run(client)

    assert task_runner.run_one_task() is True

    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["id"] == created.json()["id"]
    assert run["status"] == "completed" and run["answer"] == "Fake answer [S1]." and run["provider_model"] == "fake-model"
    with SessionLocal() as db:
        assert db.scalars(select(Task)).one().status == TaskStatus.completed
    assert task_runner.run_one_task() is False


def test_a_run_taken_over_during_the_model_call_does_not_persist_the_stale_answer(queued_mode, fake_llm):
    client = _login("port-takeover@example.com")
    project_id, created = _create_run(client)
    handed_over = {}

    def take_over_during_model_call(request):
        handed_over["second"] = _reap_and_reclaim()
        from tests.llm_replies import chat_reply

        return chat_reply("Stale answer [S1].")

    fake_llm.handler = take_over_during_model_call

    assert task_runner.run_one_task(NOW) is True

    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["id"] == created.json()["id"]
    assert run["status"] == "running" and run["answer"] is None
    assert len(fake_llm.requests) == 1
    task = _get(Task, handed_over["second"].id)
    assert task.status == TaskStatus.running and task.attempts == 2
    with SessionLocal() as db:
        actions = [event.action for event in db.scalars(select(AuditEvent))]
    assert "research_run.finished" not in actions
    assert "research_run.failed" not in actions


def test_a_run_that_needs_sources_still_finishes_normally_through_the_queue(queued_mode):
    client = _login("port-nosrc@example.com")
    project_id, _ = _create_run(client, with_source=False)

    task_runner.run_one_task()

    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["status"] == "needs_sources"
    with SessionLocal() as db:
        assert db.scalars(select(Task)).one().status == TaskStatus.completed


def test_a_crashed_worker_is_recovered_by_the_lease_and_the_run_finishes(queued_mode, fake_llm):
    client = _login("port-crash@example.com")
    project_id, created = _create_run(client)
    claimed = q.claim_next_task(NOW)  # a worker takes the task...
    assert claimed is not None
    with SessionLocal() as db:  # ...and dies mid-run, after marking the run running
        run = db.get(ResearchRun, uuid.UUID(created.json()["id"]))
        run.status, run.started_at, run.attempt_count = ResearchRunStatus.running, NOW, 1
        db.commit()

    assert q.reap_expired_tasks(NOW + timedelta(hours=1)) == 1
    assert task_runner.run_one_task(NOW + timedelta(hours=2)) is True

    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["status"] == "completed"


def test_a_run_whose_task_is_abandoned_is_marked_failed_not_left_running(queued_mode):
    client = _login("port-abandon@example.com")
    project_id, created = _create_run(client)
    with SessionLocal() as db:
        run = db.get(ResearchRun, uuid.UUID(created.json()["id"]))
        run.status = ResearchRunStatus.running
        task = db.scalars(select(Task)).one()
        task.status, task.attempts, task.max_attempts = TaskStatus.running, 3, 3
        task.lease_expires_at = NOW - timedelta(minutes=1)
        db.commit()

    assert q.reap_expired_tasks(NOW) == 1

    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["status"] == "failed" and "abandoned" in run["error_message"] and run["answer"] is None
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id)))]
    assert "research_run.abandoned" in actions and "task.failed" in actions


def test_an_unexpected_error_requeues_the_run_for_a_retry_instead_of_failing_it(queued_mode, fake_llm, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom inside the executor")

    monkeypatch.setattr(executor, "build_plan", explode)
    client = _login("port-retry-requeue@example.com")
    _, created = _create_run(client)

    assert task_runner.run_one_task(NOW) is True

    task = _only_task()
    assert task.status == TaskStatus.queued and task.attempts == 1
    assert task.run_after.replace(tzinfo=None) > NOW.replace(tzinfo=None)  # held back by the backoff
    run = _get(ResearchRun, uuid.UUID(created.json()["id"]))
    assert run.status == ResearchRunStatus.queued and "retried" in run.error_message and run.answer is None
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "task.requeued" in actions and "research_run.failed" not in actions


def test_a_retry_completes_the_run_when_the_unexpected_error_was_transient(queued_mode, fake_llm, monkeypatch):
    real_build_plan = executor.build_plan
    calls = []

    def flaky_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("a one-off failure")
        return real_build_plan(*args, **kwargs)

    monkeypatch.setattr(executor, "build_plan", flaky_once)
    client = _login("port-retry-ok@example.com")
    project_id, created = _create_run(client)

    assert task_runner.run_one_task(NOW) is True
    assert task_runner.run_one_task(NOW + timedelta(days=1)) is True

    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["status"] == "completed" and run["answer"] == "Fake answer [S1]."
    assert _only_task().status == TaskStatus.completed and len(calls) == 2


def test_an_unexpected_error_that_keeps_happening_fails_the_run_after_the_last_attempt(queued_mode, fake_llm, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom every time")

    monkeypatch.setattr(executor, "build_plan", explode)
    client = _login("port-retry-exhaust@example.com")
    project_id, created = _create_run(client)

    for day in range(3):  # the default max_attempts is 3; each attempt runs once its backoff has passed
        task_runner.run_one_task(NOW + timedelta(days=day))

    task = _only_task()
    assert task.status == TaskStatus.failed and task.attempts == task.max_attempts
    run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert run["status"] == "failed" and "abandoned" in run["error_message"] and run["answer"] is None
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id)))]
    assert "research_run.abandoned" in actions and "task.failed" in actions


def test_the_abandon_hook_leaves_an_already_finished_run_alone(queued_mode, fake_llm):
    client = _login("port-done@example.com")
    project_id, created = _create_run(client)
    task_runner.run_one_task()  # completes normally

    from app.task_handlers import _research_run_abandoned

    _research_run_abandoned(ClaimedTask(uuid.uuid4(), uuid.UUID(project_id), "research_run", {"run_id": created.json()["id"]}, 3))

    assert client.get(f"/api/projects/{project_id}/research-runs").json()[0]["status"] == "completed"


# ---- lost ownership (M0.6.9) ----------------------------------------------------------------

def _reap_and_reclaim():
    """The lease lapses, the reaper requeues the task and a second worker claims it."""
    later = NOW + timedelta(seconds=get_settings().task_lease_seconds + 1)
    assert q.reap_expired_tasks(later) == 1
    return q.claim_next_task(later + timedelta(days=1))


def test_ensure_owned_passes_for_the_owner_and_raises_once_the_task_is_taken_over():
    task_id = _task(_project(), "own-check")
    first = q.claim_next_task(NOW)
    first.ensure_owned()
    with SessionLocal() as db:
        first.ensure_owned(db)  # the in-transaction form used before a final commit

    second = _reap_and_reclaim()

    with pytest.raises(registry.TaskOwnershipLost):
        first.ensure_owned()
    assert first.lost.is_set()
    second.ensure_owned()  # the new owner is unaffected
    assert second.attempt == 2 and _get(Task, task_id).attempts == 2


def test_a_refused_heartbeat_marks_the_task_lost_without_a_database_check(monkeypatch):
    task = ClaimedTask(uuid.uuid4(), uuid.uuid4(), "beat", None, 1)
    monkeypatch.setattr(task_runner, "heartbeat", lambda task_id, attempt: False)
    stop = threading.Event()

    task_runner._heartbeat_loop(task, stop, interval=0.01)

    assert task.lost.is_set()
    monkeypatch.setattr(q, "owns_task", lambda *a, **k: pytest.fail("must not need the database"))
    with pytest.raises(registry.TaskOwnershipLost):
        task.ensure_owned()

def test_the_heartbeat_stops_renewing_once_the_type_max_run_time_has_passed(monkeypatch):
    # A hung handler must not hold its task forever: past the limit the lease is left to expire,
    # so the reaper can hand the task to another worker.
    task = ClaimedTask(uuid.uuid4(), uuid.uuid4(), "hung", None, 1)
    renewals = []
    monkeypatch.setattr(task_runner, "heartbeat", lambda task_id, attempt: renewals.append(attempt) or True)
    stop = threading.Event()  # never set: the handler is still running
    beat = threading.Thread(
        target=task_runner._heartbeat_loop, args=(task, stop, 0.01), kwargs={"max_run_seconds": 0.1}, daemon=True
    )
    beat.start()
    beat.join(timeout=3)

    assert not beat.is_alive()
    assert len(renewals) >= 1 and not task.lost.is_set()
    stop.set()



def test_a_task_type_without_its_own_limit_gets_the_default_run_time_cap(temp_handlers, monkeypatch):
    # X.115: no task type sets max_run_seconds, so a hung handler used to keep its lease forever.
    caps = {}
    monkeypatch.setattr(task_runner, "_heartbeat_loop", lambda task, stop, interval, cap=None: caps.setdefault(task.type, cap))
    monkeypatch.setattr(get_settings(), "task_max_run_seconds", 42)
    temp_handlers("uncapped", lambda task: None)
    registry.register("capped", max_run_seconds=7)(lambda task: None)
    try:
        _task(_project(), "uncapped")
        task_runner.run_one_task(NOW)
        _task(_project(), "capped")
        task_runner.run_one_task(NOW)
    finally:
        registry.HANDLERS.pop("capped", None)
        registry.MAX_RUN_SECONDS.pop("capped", None)

    assert caps == {"uncapped": 42, "capped": 7}


def test_a_worker_that_lost_its_task_reports_nothing(temp_handlers):
    project_id = _project()
    task_id = _task(project_id, "slow")
    handed_over = {}

    def slow(task):
        handed_over["second"] = _reap_and_reclaim()  # taken over mid-run
        task.ensure_owned()  # the handler's next side-effect check stops it

    temp_handlers("slow", slow)
    assert task_runner.run_one_task(NOW) is True

    task = _get(Task, task_id)
    assert task.status == TaskStatus.running and task.attempts == 2  # still the second worker's
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id))]
    assert actions == ["task.requeued"]  # only the reaper's; the stale worker neither failed nor completed it
    assert q.complete_task(task_id, handed_over["second"].attempt) is True
