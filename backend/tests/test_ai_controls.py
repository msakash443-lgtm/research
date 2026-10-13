"""Project AI controls (X.31.7): Off means no project text goes to any AI; the route decides which model is used."""
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.executor import execute_research_run
from app.agent.llm import LLMConfigurationError
from app.ai_controls import AIOffError, llm_for_project
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AiInvolvement, AiModelRoute, AuditEvent, Project, ResearchRun, ResearchRunStatus
from sqlalchemy import select


def _setup(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "U"})
    pid = client.post("/api/projects", json={"title": "AI controls"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/sources", json={"title": "Release", "evidence_excerpt": "Output rose.", "locator": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    return client, pid


def _ask(client, pid):
    return client.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output rise at all?"})


def _set_local_model(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "local_llm_api_base_url", "http://local-llm.test/v1")
    monkeypatch.setattr(settings, "local_llm_model", "local-model")
    monkeypatch.setattr(settings, "local_llm_api_key", SecretStr("local-key"))


def test_new_projects_default_to_suggestions_only_on_the_standard_model():
    client, pid = _setup("ai-default@example.com")
    body = client.get(f"/api/projects/{pid}/ai-settings").json()
    assert body["ai_involvement"] == "suggestions_only" and body["ai_model_route"] == "standard"
    assert client.get(f"/api/projects/{pid}").json()["ai_involvement"] == "suggestions_only"


def test_with_ai_off_every_ai_request_is_refused_before_anything_is_queued_or_sent(fake_llm):
    client, pid = _setup("ai-off-requests@example.com")
    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"}).status_code == 200

    block = {"label": "A", "terms": ["output"]}
    for response in (
        _ask(client, pid),
        client.post(f"/api/projects/{pid}/screening/prescreen"),
        client.post(f"/api/projects/{pid}/screening/seed-check"),
        client.post(f"/api/projects/{pid}/clusters", json={"k": 2}),
        client.post(f"/api/projects/{pid}/searches/suggest-synonyms", json={"block": block}),
    ):
        assert response.status_code == 409, response.text
    assert fake_llm.requests == []
    assert client.get(f"/api/projects/{pid}/research-runs").json() == []


def test_a_run_queued_before_ai_was_switched_off_is_failed_without_calling_the_model(fake_llm):
    client, pid = _setup("ai-off-queued@example.com")
    with SessionLocal() as db:
        run = ResearchRun(project_id=uuid.UUID(pid), question="Did output rise at all?", status=ResearchRunStatus.queued, created_by="user:x")
        db.add(run)
        db.commit()
        run_id = run.id
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"})

    execute_research_run(run_id)

    with SessionLocal() as db:
        run = db.get(ResearchRun, run_id)
        assert run.status == ResearchRunStatus.failed and "AI is turned off" in run.error_message
        assert run.answer is None and run.input_snapshot is None
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == run.project_id))]
        assert "research_run.refused_ai_off" in actions
    assert fake_llm.requests == []


def test_turning_ai_back_on_lets_runs_proceed(fake_llm):
    client, pid = _setup("ai-off-on@example.com")
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"})
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "full"})
    run = _ask(client, pid)
    assert run.status_code == 202 and run.json()["status"] == "completed"


def test_changes_are_audited_with_before_and_after_and_a_no_op_is_not(fake_llm):
    client, pid = _setup("ai-audit@example.com")
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "suggestions_only"})  # no change
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"})
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "project.ai_settings_changed")).all()
    assert len(events) == 1
    assert events[0].payload_json["from"]["ai_involvement"] == "suggestions_only" and events[0].payload_json["to"]["ai_involvement"] == "off"


def test_routing_to_the_local_model_needs_one_to_be_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "local_llm_api_base_url", None)
    monkeypatch.setattr(settings, "local_llm_model", None)
    client, pid = _setup("ai-local-missing@example.com")
    assert client.get(f"/api/projects/{pid}/ai-settings").json()["local_model_configured"] is False
    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_model_route": "local"}).status_code == 409
    assert client.get(f"/api/projects/{pid}/ai-settings").json()["ai_model_route"] == "standard"


def test_a_local_route_sends_the_run_to_the_local_endpoint_only_and_records_that_model(fake_llm, monkeypatch):
    _set_local_model(monkeypatch)
    client, pid = _setup("ai-local-run@example.com")
    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_model_route": "local"}).status_code == 200

    run = _ask(client, pid).json()

    assert run["status"] == "completed" and run["provider_model"] == "local-model"
    assert {r.url.host for r in fake_llm.requests} == {"local-llm.test"}


def test_a_local_route_with_the_local_model_removed_fails_loudly_and_never_uses_the_default_model(fake_llm, monkeypatch):
    _set_local_model(monkeypatch)
    client, pid = _setup("ai-local-gone@example.com")
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_model_route": "local"})
    monkeypatch.setattr(get_settings(), "local_llm_api_base_url", None)

    run = _ask(client, pid).json()

    assert run["status"] == "needs_configuration" and "local model" in run["error_message"]
    assert fake_llm.requests == []


def test_a_local_route_refuses_embeddings_because_they_have_no_local_only_endpoint(fake_llm, monkeypatch):
    _set_local_model(monkeypatch)
    client, pid = _setup("ai-local-embed@example.com")
    client.put(f"/api/projects/{pid}/ai-settings", json={"ai_model_route": "local"})
    response = client.post(f"/api/projects/{pid}/clusters", json={"k": 2})
    assert response.status_code == 409 and "local model" in response.json()["detail"]


def test_llm_for_project_refuses_when_off_and_does_not_fall_back_when_local_is_missing(monkeypatch):
    settings = get_settings()
    project = Project(title="x", ai_involvement=AiInvolvement.off, ai_model_route=AiModelRoute.standard)
    with pytest.raises(AIOffError):
        llm_for_project(settings, project, purpose="t")
    project.ai_involvement = AiInvolvement.full
    project.ai_model_route = AiModelRoute.local
    monkeypatch.setattr(settings, "local_llm_api_base_url", None)
    with pytest.raises(LLMConfigurationError):
        llm_for_project(settings, project, purpose="t")
