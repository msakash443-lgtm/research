"""Per-project observability (X.7): step outcomes from the task queue and model-call tokens, counts only."""
import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import LlmUsage, Task, TaskStatus
from tests.llm_replies import chat_reply


def _login_project(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "O"})
    pid = client.post("/api/projects", json={"title": "Observed"}).json()["id"]
    return client, pid


def _task(pid, type, status, attempts=1, error=None, payload=None):
    with SessionLocal() as db:
        db.add(Task(project_id=uuid.UUID(pid), type=type, status=status, attempts=attempts, error=error, payload=payload))
        db.commit()


def _metrics(client, pid):
    response = client.get(f"/api/projects/{pid}/observability")
    assert response.status_code == 200
    return response.json()


def test_an_empty_project_reports_zero_steps_and_zero_calls():
    client, pid = _login_project("obs-empty@example.com")
    assert _metrics(client, pid) == {
        "steps": [], "step_durations": [], "steps_total": 0, "steps_failed": 0, "steps_retried": 0,
        "model_calls": [], "model_calls_total": 0, "tokens_total": 0, "cost_usd_total": 0.0, "calls_unpriced_total": 0,
        "failures": [],
    }


def test_steps_are_counted_per_type_by_outcome_and_retries_are_counted():
    client, pid = _login_project("obs-steps@example.com")
    _task(pid, "research_run", TaskStatus.completed, attempts=1)
    _task(pid, "research_run", TaskStatus.failed, attempts=3)
    _task(pid, "research_run", TaskStatus.queued, attempts=0)
    _task(pid, "prescreen", TaskStatus.completed, attempts=2)

    metrics = _metrics(client, pid)
    by_type = {row["type"]: row for row in metrics["steps"]}
    assert by_type["research_run"] == {
        "type": "research_run", "total": 3, "completed": 1, "failed": 1, "queued": 1, "running": 0, "paused": 0,
        "blocked": 0, "retried": 1,
    }
    assert by_type["prescreen"]["retried"] == 1 and by_type["prescreen"]["completed"] == 1
    assert (metrics["steps_total"], metrics["steps_failed"], metrics["steps_retried"]) == (4, 1, 2)


def test_queued_running_and_paused_steps_are_counted_in_their_own_columns():
    client, pid = _login_project("obs-waiting@example.com")
    _task(pid, "research_run", TaskStatus.queued, attempts=0)
    _task(pid, "research_run", TaskStatus.running, attempts=1)
    _task(pid, "research_run", TaskStatus.paused, attempts=1)
    _task(pid, "research_run", TaskStatus.paused, attempts=1)

    row = {r["type"]: r for r in _metrics(client, pid)["steps"]}["research_run"]
    assert (row["queued"], row["running"], row["paused"]) == (1, 1, 2)
    assert row["total"] == 4


def test_error_text_is_never_returned():
    client, pid = _login_project("obs-noerr@example.com")
    _task(pid, "research_run", TaskStatus.failed, attempts=3, error="provider said: sk-SECRET-MARKER in prompt")
    assert "SECRET-MARKER" not in client.get(f"/api/projects/{pid}/observability").text


def test_another_projects_steps_are_not_counted():
    client, pid = _login_project("obs-iso-a@example.com")
    _, other = _login_project("obs-iso-b@example.com")
    _task(other, "research_run", TaskStatus.failed, attempts=3)
    assert _metrics(client, pid)["steps_total"] == 0


def test_a_research_run_shows_its_model_call_tokens_by_purpose_and_model(fake_llm):
    body = chat_reply("Output rose [S1].")
    body["usage"] = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    fake_llm.handler = lambda request: body
    client, pid = _login_project("obs-llm@example.com")
    sid = client.post(f"/api/projects/{pid}/sources", json={"title": "Release", "evidence_excerpt": "Output rose.", "locator": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    run = client.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output rise at all?"}).json()
    assert run["status"] == "completed"

    metrics = _metrics(client, pid)
    assert metrics["model_calls_total"] == 1 and metrics["tokens_total"] == 150
    assert metrics["model_calls"][0]["purpose"] == "research_run"
    assert metrics["model_calls"][0]["calls_without_token_counts"] == 0


def test_a_model_call_still_in_flight_is_not_shown_as_a_call():
    client, pid = _login_project("obs-inflight@example.com")
    with SessionLocal() as db:  # a call holding its budget, not yet answered (X.110)
        db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="", reserved_tokens=600))
        db.commit()
    metrics = _metrics(client, pid)
    assert metrics["model_calls"] == [] and metrics["model_calls_total"] == 0


def test_a_completed_step_reports_how_long_its_final_attempt_took(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app import task_queue

    client, pid = _login_project("obs-duration@example.com")
    t0 = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        task = Task(project_id=uuid.UUID(pid), type="timed_step", status=TaskStatus.queued, attempts=0, max_attempts=3)
        db.add(task)
        db.commit()
        task_id = task.id

    # Attempt 1 fails and is requeued with backoff; attempt 2 succeeds. Only attempt 2 is timed.
    first = task_queue.claim_next_task(now=t0)
    assert first is not None and first.id == task_id
    task_queue.fail_task(task_id, first.attempt, "provider timeout", now=t0 + timedelta(seconds=5))
    second = task_queue.claim_next_task(now=t0 + timedelta(seconds=100))
    assert second is not None and second.attempt == 2
    monkeypatch.setattr(task_queue, "utcnow", lambda: t0 + timedelta(seconds=130))
    assert task_queue.complete_task(task_id, second.attempt)

    durations = {row["type"]: row for row in _metrics(client, pid)["step_durations"]}
    assert durations["timed_step"] == {"type": "timed_step", "timed": 1, "avg_seconds": 30.0, "max_seconds": 30.0}


def test_a_step_that_failed_for_good_has_no_duration_and_no_timing_is_invented():
    client, pid = _login_project("obs-noduration@example.com")
    _task(pid, "untimed_step", TaskStatus.completed, attempts=1)
    _task(pid, "untimed_step", TaskStatus.failed, attempts=3)

    durations = {row["type"]: row for row in _metrics(client, pid)["step_durations"]}
    assert durations["untimed_step"]["timed"] == 0
    assert durations["untimed_step"]["avg_seconds"] is None and durations["untimed_step"]["max_seconds"] is None


def _queued(pid, type, max_attempts=3):
    with SessionLocal() as db:
        task = Task(project_id=uuid.UUID(pid), type=type, status=TaskStatus.queued, attempts=0, max_attempts=max_attempts)
        db.add(task)
        db.commit()
        return task.id


def _as_utc(moment):
    from datetime import timezone

    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes


def test_a_step_that_failed_for_good_after_running_is_not_timed_even_though_it_has_a_start_and_a_finish(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app import task_queue

    client, pid = _login_project("obs-failedtimed@example.com")
    t0 = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)

    failed_id = _queued(pid, "mixed_step")
    failed = task_queue.claim_next_task(now=t0)
    assert failed is not None and failed.id == failed_id
    assert task_queue.fail_task(failed_id, failed.attempt, "bad input", now=t0 + timedelta(seconds=20), permanent=True) == TaskStatus.failed

    done_id = _queued(pid, "mixed_step")
    done = task_queue.claim_next_task(now=t0 + timedelta(seconds=60))
    assert done is not None and done.id == done_id
    monkeypatch.setattr(task_queue, "utcnow", lambda: t0 + timedelta(seconds=90))
    assert task_queue.complete_task(done_id, done.attempt)

    durations = {row["type"]: row for row in _metrics(client, pid)["step_durations"]}
    assert durations["mixed_step"] == {"type": "mixed_step", "timed": 1, "avg_seconds": 30.0, "max_seconds": 30.0}


def test_a_step_that_fails_for_good_records_when_it_finished_whether_the_worker_reported_it_or_the_lease_ran_out():
    from datetime import datetime, timedelta, timezone

    from app import task_queue
    from app.config import get_settings

    _, pid = _login_project("obs-finished@example.com")
    t0 = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)

    reported_id = _queued(pid, "reported_step")
    reported = task_queue.claim_next_task(now=t0)
    assert reported is not None and reported.id == reported_id
    task_queue.fail_task(reported_id, reported.attempt, "bad input", now=t0 + timedelta(seconds=7), permanent=True)

    reaped_id = _queued(pid, "reaped_step", max_attempts=1)
    reaped = task_queue.claim_next_task(now=t0)
    assert reaped is not None and reaped.id == reaped_id
    reap_at = t0 + timedelta(seconds=get_settings().task_lease_seconds + 1)
    assert task_queue.reap_expired_tasks(now=reap_at) == 1

    with SessionLocal() as db:
        for task_id, finished in ((reported_id, t0 + timedelta(seconds=7)), (reaped_id, reap_at)):
            task = db.get(Task, task_id)
            assert task.status == TaskStatus.failed
            assert task.finished_at is not None and _as_utc(task.finished_at) == finished
            assert _as_utc(task.started_at) == t0


def test_model_calls_show_their_cost_and_say_how_many_calls_had_no_price():
    client, pid = _login_project("obs-cost@example.com")
    with SessionLocal() as db:
        db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="claude-sonnet-5-5",
                        prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000))
        db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="some-local-model",
                        prompt_tokens=10, completion_tokens=10, total_tokens=20))
        db.commit()
    metrics = _metrics(client, pid)
    by_model = {row["model"]: row for row in metrics["model_calls"]}
    assert by_model["claude-sonnet-5-5"]["cost_usd"] == 7.0 and by_model["claude-sonnet-5-5"]["calls_unpriced"] == 0
    assert by_model["some-local-model"]["cost_usd"] is None and by_model["some-local-model"]["calls_unpriced"] == 1
    assert metrics["cost_usd_total"] == 7.0 and metrics["calls_unpriced_total"] == 1


def test_failed_steps_are_listed_with_type_and_attempts_but_never_their_error_text():
    client, pid = _login_project("obs-failures@example.com")
    _task(pid, "research_run", TaskStatus.completed)
    _task(pid, "research_run", TaskStatus.failed, attempts=3, error="secret model text")
    _task(pid, "prescreen", TaskStatus.failed, attempts=1, error="another secret")

    response = client.get(f"/api/projects/{pid}/observability")
    assert "secret" not in response.text
    failures = response.json()["failures"]
    assert sorted((f["type"], f["attempts"]) for f in failures) == [("prescreen", 1), ("research_run", 3)]
    assert all(f["id"] and set(f) == {"id", "type", "finished_at", "attempts", "run_id"} for f in failures)


def test_a_failed_research_run_row_carries_its_run_id_and_other_steps_do_not():
    client, pid = _login_project("obs-run-id@example.com")
    _task(pid, "research_run", TaskStatus.failed, attempts=1, payload={"run_id": "11111111-2222-3333-4444-555555555555"})
    _task(pid, "prescreen", TaskStatus.failed, attempts=1, payload={"run_id": "99999999-2222-3333-4444-555555555555"})
    _task(pid, "research_run", TaskStatus.failed, attempts=1, payload=None)

    failures = _metrics(client, pid)["failures"]
    by_type = sorted(((f["type"], f["run_id"]) for f in failures), key=lambda pair: (pair[0], pair[1] or ""))
    assert len(by_type) == 3 and set(by_type) == {
        ("prescreen", None),
        ("research_run", "11111111-2222-3333-4444-555555555555"),
        ("research_run", None),
    }


def test_the_failure_list_is_capped_at_twenty_rows():
    client, pid = _login_project("obs-cap@example.com")
    for _ in range(25):
        _task(pid, "research_run", TaskStatus.failed)
    assert len(_metrics(client, pid)["failures"]) == 20


def _task_created(pid, type, status, created_at):
    with SessionLocal() as db:
        db.add(Task(project_id=uuid.UUID(pid), type=type, status=status, attempts=1, created_at=created_at))
        db.commit()


def test_since_is_inclusive_and_until_is_exclusive_for_steps():
    from datetime import datetime, timezone

    client, pid = _login_project("obs-window@example.com")
    start = datetime(2026, 10, 10, tzinfo=timezone.utc)
    end = datetime(2026, 10, 20, tzinfo=timezone.utc)
    _task_created(pid, "in_window", TaskStatus.completed, start)
    _task_created(pid, "in_window", TaskStatus.completed, datetime(2026, 10, 15, tzinfo=timezone.utc))
    _task_created(pid, "at_until", TaskStatus.completed, end)
    _task_created(pid, "before_since", TaskStatus.completed, datetime(2026, 10, 1, tzinfo=timezone.utc))
    metrics = client.get(
        f"/api/projects/{pid}/observability", params={"since": start.isoformat(), "until": end.isoformat()}
    ).json()
    assert [(row["type"], row["total"]) for row in metrics["steps"]] == [("in_window", 2)]
    assert metrics["steps_total"] == 2


def test_the_window_also_limits_the_failure_list():
    from datetime import datetime, timezone

    client, pid = _login_project("obs-window-fail@example.com")
    _task_created(pid, "old_failure", TaskStatus.failed, datetime(2026, 9, 1, tzinfo=timezone.utc))
    _task_created(pid, "new_failure", TaskStatus.failed, datetime(2026, 10, 15, tzinfo=timezone.utc))
    failures = client.get(
        f"/api/projects/{pid}/observability", params={"since": "2026-10-01T00:00:00Z"}
    ).json()["failures"]
    assert [row["type"] for row in failures] == ["new_failure"]


def test_the_window_limits_model_calls_by_when_the_call_was_made():
    from datetime import datetime, timezone

    client, pid = _login_project("obs-window-calls@example.com")
    with SessionLocal() as db:
        for day in (5, 15):
            db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="m",
                            prompt_tokens=1, completion_tokens=1, total_tokens=2,
                            created_at=datetime(2026, 10, day, tzinfo=timezone.utc)))
        db.commit()
    metrics = client.get(
        f"/api/projects/{pid}/observability", params={"since": "2026-10-10T00:00:00Z"}
    ).json()
    assert metrics["model_calls_total"] == 1


def test_a_window_whose_until_is_not_after_since_is_rejected():
    client, pid = _login_project("obs-window-bad@example.com")
    url = f"/api/projects/{pid}/observability"
    assert client.get(url, params={"since": "2026-10-20T00:00:00Z", "until": "2026-10-10T00:00:00Z"}).status_code == 422
    assert client.get(url, params={"since": "2026-10-10T00:00:00Z", "until": "2026-10-10T00:00:00Z"}).status_code == 422


def test_a_time_without_an_offset_is_read_as_utc():
    from datetime import datetime, timezone

    client, pid = _login_project("obs-window-utc@example.com")
    _task_created(pid, "utc_step", TaskStatus.completed, datetime(2026, 10, 15, tzinfo=timezone.utc))
    url = f"/api/projects/{pid}/observability"
    assert client.get(url, params={"since": "2026-10-15T00:00:00"}).json()["steps_total"] == 1
    assert client.get(url, params={"since": "2026-10-15T01:00:00+01:00"}).json()["steps_total"] == 1
    assert client.get(url, params={"until": "2026-10-15T00:00:00"}).json()["steps_total"] == 0


def test_the_window_limits_step_durations_to_steps_created_in_it():
    from datetime import datetime, timedelta, timezone

    client, pid = _login_project("obs-window-durations@example.com")
    inside = datetime(2026, 10, 15, tzinfo=timezone.utc)
    outside = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        for type, created, seconds in (("timed", inside, 10), ("timed", outside, 100), ("old_only", outside, 5)):
            db.add(Task(project_id=uuid.UUID(pid), type=type, status=TaskStatus.completed, attempts=1, created_at=created,
                        started_at=created, finished_at=created + timedelta(seconds=seconds)))
        db.commit()
    durations = client.get(
        f"/api/projects/{pid}/observability", params={"since": "2026-10-10T00:00:00Z"}
    ).json()["step_durations"]
    assert durations == [{"type": "timed", "timed": 1, "avg_seconds": 10.0, "max_seconds": 10.0}]


def test_the_window_limits_the_cost_of_model_calls_to_calls_made_in_it():
    from datetime import datetime, timezone

    client, pid = _login_project("obs-window-cost@example.com")
    with SessionLocal() as db:
        for day in (5, 15):
            db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="claude-sonnet-5-5",
                            prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000,
                            created_at=datetime(2026, 10, day, tzinfo=timezone.utc)))
        db.commit()
    metrics = client.get(
        f"/api/projects/{pid}/observability", params={"since": "2026-10-10T00:00:00Z"}
    ).json()
    assert metrics["model_calls"][0]["cost_usd"] == 7.0
    assert metrics["cost_usd_total"] == 7.0


def test_a_time_that_has_no_utc_equivalent_is_rejected_not_a_server_error():
    client, pid = _login_project("obs-window-overflow@example.com")
    url = f"/api/projects/{pid}/observability"
    assert client.get(url, params={"since": "0001-01-01T00:00:00+05:00"}).status_code == 422
    assert client.get(url, params={"until": "9999-12-31T23:59:59-05:00"}).status_code == 422
