"""Token usage is recorded per call and a project's token budget is a hard stop (plan M0.9.1)."""
import httpx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.llm_usage import parse_usage
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
