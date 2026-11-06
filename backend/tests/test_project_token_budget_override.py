"""Per-project token-budget override (plan M0.9.3): an owner can tighten or loosen the global
`PROJECT_TOKEN_BUDGET` for one project; everyone else sees the effective budget on `GET .../usage`.
"""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Project
from tests.llm_replies import chat_reply


def _with_usage(answer, prompt=100, completion=50):
    body = chat_reply(answer)
    body["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    return body


def _setup(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "U"})
    pid = client.post("/api/projects", json={"title": "Budget override"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/sources", json={"title": "Release", "evidence_excerpt": "Output rose.", "locator": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    return client, pid


def _run(client, pid):
    response = client.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output rise at all?"})
    assert response.status_code == 202
    return response.json()  # inline mode: the run has already finished


def test_with_no_override_the_effective_budget_is_the_global_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 500)
    client, pid = _setup("budget-default@example.com")
    usage = client.get(f"/api/projects/{pid}/usage").json()
    assert usage["token_budget"] == 500 and usage["budget_override"] is None and usage["default_budget"] == 500


def test_owner_can_set_an_override_that_is_tighter_than_the_global_default(fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 10_000)
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].")
    client, pid = _setup("budget-tight@example.com")
    response = client.put(f"/api/projects/{pid}/usage/budget", json={"override": 120})
    assert response.status_code == 200
    body = response.json()
    assert body["token_budget"] == 120 and body["budget_override"] == 120 and body["default_budget"] == 10_000

    assert _run(client, pid)["status"] == "completed"  # 150 used; the check happens before a call, so it may overshoot
    run = _run(client, pid)
    assert run["status"] == "needs_configuration" and "token budget" in run["error_message"]


def test_owner_can_set_an_override_looser_than_a_global_default_that_would_have_blocked_it(fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 100)
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].")
    client, pid = _setup("budget-loose@example.com")
    assert client.put(f"/api/projects/{pid}/usage/budget", json={"override": 0}).json()["token_budget"] is None

    assert _run(client, pid)["status"] == "completed"
    assert _run(client, pid)["status"] == "completed"  # would have been blocked at the global 100-token budget


def test_clearing_the_override_returns_to_the_global_default(monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 300)
    client, pid = _setup("budget-clear@example.com")
    client.put(f"/api/projects/{pid}/usage/budget", json={"override": 50})
    assert client.get(f"/api/projects/{pid}/usage").json()["token_budget"] == 50

    cleared = client.put(f"/api/projects/{pid}/usage/budget", json={"override": None})
    assert cleared.json()["budget_override"] is None and cleared.json()["token_budget"] == 300


def test_a_negative_override_is_refused():
    client, pid = _setup("budget-negative@example.com")
    response = client.put(f"/api/projects/{pid}/usage/budget", json={"override": -1})
    assert response.status_code == 422


def test_the_override_is_per_project_not_global(fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 10_000)
    fake_llm.handler = lambda r: _with_usage("Output rose [S1].")
    a, pa = _setup("budget-scope-a@example.com")
    b, pb = _setup("budget-scope-b@example.com")
    a.put(f"/api/projects/{pa}/usage/budget", json={"override": 120})

    assert _run(a, pa)["status"] == "completed"
    assert _run(a, pa)["status"] == "needs_configuration"  # project a is over its own override
    assert _run(b, pb)["status"] == "completed"  # project b is unaffected, still on the 10,000 default


def test_setting_the_override_is_audited():
    client, pid = _setup("budget-audit@example.com")
    client.put(f"/api/projects/{pid}/usage/budget", json={"override": 200})
    events = client.get(f"/api/projects/{pid}/audit").json()
    changed = [e for e in events if e["action"] == "project.budget_override_changed"]
    assert len(changed) == 1
    assert changed[0]["payload_json"] == {"from": None, "to": 200}


def test_the_override_survives_in_the_database_as_a_plain_column(monkeypatch):
    monkeypatch.setattr(get_settings(), "project_token_budget", 1000)
    client, pid = _setup("budget-column@example.com")
    client.put(f"/api/projects/{pid}/usage/budget", json={"override": 777})
    with SessionLocal() as db:
        import uuid as _uuid
        project = db.get(Project, _uuid.UUID(pid))
        assert project.token_budget_override == 777
