"""Runs with web retrieval have their own, lower daily cap (plan M0.9.2)."""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _setup(monkeypatch, email, retrieval_cap, overall_cap=20):
    settings = get_settings()
    monkeypatch.setattr(settings, "arc_retrieval_enabled", True)
    monkeypatch.setattr(settings, "research_run_retrieval_limit_per_day", retrieval_cap)
    monkeypatch.setattr(settings, "research_run_limit_per_day", overall_cap)
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "C"})
    return client, client.post("/api/projects", json={"title": "Cap"}).json()["id"]


def _post(client, pid, web):
    return client.post(f"/api/projects/{pid}/research-runs", json={"question": "Does the cap hold up?", "use_web_retrieval": web})


def test_retrieval_runs_stop_at_their_own_cap_while_plain_runs_continue(monkeypatch):
    client, pid = _setup(monkeypatch, "cap1@example.com", retrieval_cap=2)
    assert [_post(client, pid, True).status_code for _ in range(2)] == [202, 202]
    blocked = _post(client, pid, True)
    assert blocked.status_code == 429 and "web retrieval" in blocked.json()["detail"]
    assert _post(client, pid, False).status_code == 202


def test_plain_runs_do_not_use_up_the_retrieval_cap(monkeypatch):
    client, pid = _setup(monkeypatch, "cap2@example.com", retrieval_cap=1)
    for _ in range(3):
        assert _post(client, pid, False).status_code == 202
    assert _post(client, pid, True).status_code == 202


def test_a_cap_of_zero_turns_retrieval_runs_off_without_touching_plain_runs(monkeypatch):
    client, pid = _setup(monkeypatch, "cap3@example.com", retrieval_cap=0)
    assert _post(client, pid, True).status_code == 429
    assert _post(client, pid, False).status_code == 202


def test_the_caps_are_per_project(monkeypatch):
    a, pa = _setup(monkeypatch, "cap4@example.com", retrieval_cap=1)
    _post(a, pa, True)
    b, pb = _setup(monkeypatch, "cap5@example.com", retrieval_cap=1)
    assert _post(b, pb, True).status_code == 202


def test_the_overall_daily_limit_still_applies_to_retrieval_runs(monkeypatch):
    client, pid = _setup(monkeypatch, "cap6@example.com", retrieval_cap=5, overall_cap=1)
    assert _post(client, pid, False).status_code == 202
    assert _post(client, pid, True).status_code == 429
