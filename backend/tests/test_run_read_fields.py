from fastapi.testclient import TestClient

from app.agent import executor
from app.agent.llm import LLMResponseError, OpenAICompatibleLLM
from app.config import get_settings
from app.main import app

QUESTION = "What does the evidence say about female labour participation?"


def _client_with_source():
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": "snap@example.com", "display_name": "S"}).status_code == 200
    project_id = client.post("/api/projects", json={"title": "Snapshot"}).json()["id"]
    source = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Official release", "evidence_excerpt": "Participation rose.", "locator": "p. 1"},
    )
    assert source.status_code == 201
    # Cited sources must be verified (citation guard, M1.10.3).
    assert client.post(f"/api/projects/{project_id}/sources/{source.json()['id']}/verify").status_code == 200
    return client, project_id


def _post_run(client, project_id):
    response = client.post(f"/api/projects/{project_id}/research-runs", json={"question": QUESTION})
    assert response.status_code == 202
    return response.json()


def test_run_exposes_input_snapshot(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_model", "test-model")
    monkeypatch.setattr(OpenAICompatibleLLM, "complete", lambda self, system, user: "Answer [S1].")
    client, project_id = _client_with_source()

    run = _post_run(client, project_id)

    assert run["status"] == "completed"
    assert run["provider_model"] == "test-model"
    assert run["input_snapshot"]["sources"][0]["title"] == "Official release"
    listed = client.get(f"/api/projects/{project_id}/research-runs").json()
    assert listed[0]["input_snapshot"] == run["input_snapshot"]


def test_failed_run_still_records_provider_model(monkeypatch):
    def fail(self, system, user):
        raise LLMResponseError("provider unavailable")

    monkeypatch.setattr(get_settings(), "llm_model", "test-model")
    monkeypatch.setattr(OpenAICompatibleLLM, "complete", fail)
    client, project_id = _client_with_source()

    run = _post_run(client, project_id)

    assert run["status"] == "failed"
    assert run["error_message"] == "provider unavailable"
    assert run["answer"] is None
    assert run["provider_model"] == "test-model"
