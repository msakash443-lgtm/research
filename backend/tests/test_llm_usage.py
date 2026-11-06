"""Token usage is recorded per call and a project's token budget is a hard stop (plan M0.9.1)."""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.llm import LLMResponseError, OpenAICompatibleLLM
from app.config import get_settings
from app.database import SessionLocal
from app.llm_usage import LLMBudgetExceeded, ProjectMeter, parse_usage
from app.models import LlmUsage
from app.main import app
from tests.llm_replies import chat_reply


def _with_usage(answer, prompt=100, completion=50):
    body = chat_reply(answer)
    body["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    return body


def _setup(email="usage@example.com"):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "U"})
    pid = client.post("/api/projects", json={"title": "Usage"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/sources", json={"title": "Release", "evidence_excerpt": "Output rose.", "locator": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    return client, pid


def _run(client, pid):
    response = client.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output rise at all?"})
    assert response.status_code == 202
    return response.json()  # inline mode: the run has already finished


def test_a_run_records_the_tokens_the_provider_reported(fake_llm):
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].")
    client, pid = _setup()
    _run(client, pid)
    usage = client.get(f"/api/projects/{pid}/usage").json()
    assert usage["tokens_used"] == 150 and usage["calls"] == 1 and usage["token_budget"] is None
    assert usage["by_purpose"] == [{"purpose": "research_run", "calls": 1, "tokens": 150}]


def test_a_provider_that_reports_no_usage_is_recorded_as_unknown_not_zero(fake_llm):
    client, pid = _setup("usage-none@example.com")
    _run(client, pid)
    usage = client.get(f"/api/projects/{pid}/usage").json()
    assert usage["calls"] == 1 and usage["calls_without_token_counts"] == 1 and usage["tokens_used"] == 0


def test_every_retry_of_a_malformed_reply_is_counted(fake_llm):
    body = {"choices": [{"message": {"content": "prose"}}], "usage": {"total_tokens": 10}}
    fake_llm.handler = lambda r: body
    client, pid = _setup("usage-retry@example.com")
    assert _run(client, pid)["status"] == "failed"
    assert client.get(f"/api/projects/{pid}/usage").json()["calls"] == get_settings().llm_schema_max_attempts


def test_a_project_over_its_budget_gets_no_further_model_call(fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 120)
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].")
    client, pid = _setup("usage-budget@example.com")
    assert _run(client, pid)["status"] == "completed"  # 150 used; the check happens before a call, so it may overshoot
    calls = len(fake_llm.requests)
    run = _run(client, pid)
    assert run["status"] == "needs_configuration" and "token budget" in run["error_message"] and run["answer"] is None
    assert len(fake_llm.requests) == calls  # the second run never reached the model
    assert client.get(f"/api/projects/{pid}/usage").json()["token_budget"] == 120


def test_the_budget_is_per_project(fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 120)
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].")
    a, pa = _setup("usage-a@example.com")
    b, pb = _setup("usage-b@example.com")
    _run(a, pa)
    assert _run(b, pb)["status"] == "completed"


def test_usage_parsing_ignores_bad_values():
    assert parse_usage({"usage": {"prompt_tokens": 3, "completion_tokens": 4}}) == (3, 4, 7)
    assert parse_usage({"usage": {"total_tokens": -5, "prompt_tokens": True}}) == (None, None, None)
    assert parse_usage({"usage": "lots"}) == (None, None, None) and parse_usage(None) == (None, None, None)



# ---- how close the project is to its budget (X.79.1) -------------------------------------------------------

def test_budget_status_names_ok_near_over_and_no_limit():
    from app.llm_usage import budget_status

    assert budget_status(500, None) == {"status": "no_limit", "remaining": None, "percent_used": None}
    assert budget_status(500, 0)["status"] == "no_limit"
    assert budget_status(0, 1000) == {"status": "ok", "remaining": 1000, "percent_used": 0.0}
    assert budget_status(799, 1000)["status"] == "ok"
    assert budget_status(800, 1000) == {"status": "near", "remaining": 200, "percent_used": 80.0}
    assert budget_status(1000, 1000) == {"status": "over", "remaining": 0, "percent_used": 100.0}
    assert budget_status(1500, 1000) == {"status": "over", "remaining": 0, "percent_used": 150.0}  # one call can overshoot


def test_the_usage_api_reports_status_remaining_and_percent_and_the_status_matches_the_hard_stop(fake_llm):
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].", prompt=100, completion=50)  # 150 tokens per run
    client, pid = _setup("usage-status@example.com")
    assert client.put(f"/api/projects/{pid}/usage/budget", json={"override": 180}).json()["budget_status"] == "ok"

    _run(client, pid)
    near = client.get(f"/api/projects/{pid}/usage").json()
    assert (near["budget_status"], near["tokens_remaining"], near["percent_used"]) == ("near", 30, 83.3)

    _run(client, pid)  # still under the budget when it started, so it ran and overshot
    over = client.get(f"/api/projects/{pid}/usage").json()
    assert (over["budget_status"], over["tokens_remaining"]) == ("over", 0)
    refused = client.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output rise at all?"}).json()
    assert refused["status"] != "completed"  # the same "over" the status reported: the meter refuses the call


# ---- concurrent calls share the budget (X.110) ------------------------------------------------------------

def _meter(pid, budget, monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", budget)
    return ProjectMeter(uuid.UUID(pid), get_settings(), purpose="research_run")


def test_a_call_in_flight_holds_its_reservation_so_a_concurrent_call_cannot_also_pass(monkeypatch):
    client, pid = _setup("usage-reserve@example.com")
    first, second, third = (_meter(pid, 1000, monkeypatch) for _ in range(3))
    first.before_call(600)
    second.before_call(600)  # 0 + 600 < 1000 at the moment it asks, so it is admitted and overshoots only when it runs
    with pytest.raises(LLMBudgetExceeded):
        third.before_call(600)  # 1200 reserved already, so the third is refused


def test_a_finished_call_replaces_its_reservation_with_the_tokens_it_used(monkeypatch):
    client, pid = _setup("usage-finish@example.com")
    first, second = _meter(pid, 1000, monkeypatch), _meter(pid, 1000, monkeypatch)
    ticket = first.before_call(600)
    first.after_call("m", _with_usage("ok", prompt=100, completion=50), ticket)  # 150 used, reservation gone
    second.before_call(800)  # 150 + 800 < 1000: admitted, so the 600 was not still held
    assert client.get(f"/api/projects/{pid}/usage").json()["tokens_used"] == 150


def test_a_call_that_fails_before_answering_releases_its_reservation(fake_llm, monkeypatch):
    client, pid = _setup("usage-abort@example.com")
    fake_llm.handler = lambda r: httpx.Response(400, json={})  # a non-transient failure: no retry
    meter = _meter(pid, 1000, monkeypatch)
    with pytest.raises(LLMResponseError):
        OpenAICompatibleLLM(get_settings(), meter=meter).complete("system", "user", max_tokens=600)
    assert client.get(f"/api/projects/{pid}/usage").json()["calls"] == 0  # the failed call left no row behind
    _meter(pid, 1000, monkeypatch).before_call(999)  # nothing left held, so this is admitted


def test_a_reservation_left_by_a_dead_process_stops_counting_after_its_window(monkeypatch):
    client, pid = _setup("usage-dead@example.com")
    with SessionLocal() as db:  # what a killed process leaves behind: a hold from years ago, never finished
        db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="", reserved_tokens=1000,
                        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    assert _meter(pid, 1000, monkeypatch).before_call(10) is not None  # admitted: the old hold no longer blocks the project


def test_a_reservation_just_inside_the_window_still_blocks_and_one_just_outside_it_does_not(monkeypatch):
    from app.agent.llm import MAX_RETRY_AFTER_SECONDS, TRANSIENT_ATTEMPTS
    from app.llm_usage import reservation_window

    settings = get_settings()
    longest = max(settings.llm_timeout_seconds, settings.local_llm_timeout_seconds)
    window = timedelta(seconds=TRANSIENT_ATTEMPTS * (longest + MAX_RETRY_AFTER_SECONDS) + 60)  # the length, written out
    assert reservation_window(settings) == window  # so a changed length fails here, not only at the boundary
    client, pid = _setup("usage-boundary@example.com")
    margin = timedelta(minutes=1)  # well inside the window on one side, well outside it on the other
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="", reserved_tokens=1000,
                        created_at=now - window + margin))  # younger than the window: a live call, still held
        db.commit()
    with pytest.raises(LLMBudgetExceeded):
        _meter(pid, 1000, monkeypatch).before_call(10)
    with SessionLocal() as db:
        db.query(LlmUsage).filter(LlmUsage.project_id == uuid.UUID(pid)).delete()  # clear the live hold
        db.add(LlmUsage(project_id=uuid.UUID(pid), purpose="research_run", model="", reserved_tokens=1000,
                        created_at=now - window - margin))  # older than the window: a dead hold, no longer counted
        db.commit()
    assert _meter(pid, 1000, monkeypatch).before_call(10) is not None


def test_the_usage_api_counts_a_call_in_flight_as_held_budget_not_as_a_call(monkeypatch):
    client, pid = _setup("usage-inflight@example.com")
    _meter(pid, 1000, monkeypatch).before_call(600)  # still in flight
    usage = client.get(f"/api/projects/{pid}/usage").json()
    assert (usage["calls"], usage["calls_without_token_counts"]) == (0, 0)
    assert (usage["tokens_used"], usage["budget_status"], usage["tokens_remaining"]) == (0, "ok", 400)
