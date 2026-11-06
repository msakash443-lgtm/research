import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import LLMConfigurationError
from app.main import app
from app.routers.idea_brief import get_embedder


class FakeEmbedder:
    model = "test-embed"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        # Non-zero by construction: a constant component plus word counts, so similar wording gives similar vectors.
        return [[1.0 + t.lower().count("teams"), 1.0 + t.lower().count("trust"), 1.0] for t in texts]


@pytest.fixture
def fake():
    embedder = FakeEmbedder()
    app.dependency_overrides[get_embedder] = lambda: embedder
    yield embedder
    app.dependency_overrides.pop(get_embedder, None)


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"sem-{uuid.uuid4().hex}@example.com", "display_name": "S"})
    return c


def _project(client: TestClient) -> str:
    return client.post("/api/projects", json={"title": "Semantic"}).json()["id"]


def _idea(client: TestClient, pid: str, content: str) -> None:
    assert client.post(f"/api/projects/{pid}/context", json={"kind": "idea", "content": content}).status_code in (200, 201)


def _source(client: TestClient, pid: str, title: str) -> None:
    assert client.post(f"/api/projects/{pid}/sources", json={"title": title, "year": 2020}).status_code in (200, 201)


def test_comparators_are_ranked_by_meaning_and_the_model_is_recorded(client, fake):
    pid = _project(client)
    _idea(client, pid, "Teams stall without trust")
    _source(client, pid, "Trust in teams")
    _source(client, pid, "Memory of cats")
    body = client.get(f"/api/projects/{pid}/idea-brief/semantic-novelty").json()
    assert body["model_id"] == "test-embed"
    idea = body["ideas"][0]
    assert idea["comparators"][0]["title"] == "Trust in teams"
    assert idea["distinctiveness"] is not None
    assert "not" in body["note"].lower()  # says it is a comparison, not a verdict


def test_no_sources_means_no_score_and_no_embedding_call(client, fake):
    pid = _project(client)
    _idea(client, pid, "Teams stall")
    body = client.get(f"/api/projects/{pid}/idea-brief/semantic-novelty").json()
    assert body["ideas"][0]["distinctiveness"] is None
    assert fake.calls == 0


def test_no_ideas_means_no_embedding_call(client, fake):
    pid = _project(client)
    _source(client, pid, "Trust in teams")
    assert client.get(f"/api/projects/{pid}/idea-brief/semantic-novelty").json()["ideas"] == []
    assert fake.calls == 0


def test_ai_off_is_refused_before_any_embedding(client, fake):
    pid = _project(client)
    _idea(client, pid, "Teams stall")
    _source(client, pid, "Trust in teams")
    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"}).status_code == 200
    assert client.get(f"/api/projects/{pid}/idea-brief/semantic-novelty").status_code == 409
    assert fake.calls == 0


def test_no_embedding_model_configured_fails_loudly(client):
    class Unconfigured:
        model = None

        def embed(self, texts):
            raise LLMConfigurationError("Set EMBEDDING_MODEL.")

    app.dependency_overrides[get_embedder] = lambda: Unconfigured()
    try:
        pid = _project(client)
        _idea(client, pid, "Teams stall")
        _source(client, pid, "Trust in teams")
        r = client.get(f"/api/projects/{pid}/idea-brief/semantic-novelty")
        assert r.status_code == 503
        assert "EMBEDDING_MODEL" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_embedder, None)
