import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.agent import executor
from app.agent.arc_client import ArcRetrievedSource
from app.agent.llm import LLMResponseError, OpenAICompatibleLLM
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, ResearchRun, User


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, user_id


def _events(project_id=None):
    with SessionLocal() as db:
        query = select(AuditEvent).order_by(AuditEvent.timestamp.asc(), text("rowid"))  # rowid breaks same-second ties (SQLite tests)
        if project_id:
            query = query.where(AuditEvent.project_id == uuid.UUID(project_id))
        events = db.scalars(query).all()
        return [(e.action, e.actor, e.payload_json, e.model_id) for e in events]


def test_user_writes_are_recorded_with_the_acting_user():
    client, user_id = _login("audit-user@example.com")
    _login("audit-peer@example.com")
    project_id = client.post("/api/projects", json={"title": "Audited"}).json()["id"]
    client.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"})
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "text"}).json()
    client.post(f"/api/projects/{project_id}/sources/{source['id']}/verify")
    client.post(f"/api/projects/{project_id}/members", json={"email": "audit-peer@example.com", "role": "reviewer"})
    peer_id = client.get(f"/api/projects/{project_id}/members").json()[1]["user_id"]
    client.delete(f"/api/projects/{project_id}/members/{peer_id}")

    events = _events(project_id)

    assert [e[0] for e in events] == [
        "project.created", "context.added", "source.created", "source.verified", "member.invited", "member.removed",
    ]
    assert {e[1] for e in events} == {user_id}
    assert events[3][2] == {"source_id": source["id"], "method": "human", "confirmed_automatic_check": False}
    assert events[4][2]["role"] == "reviewer"


def test_failed_or_repeated_actions_do_not_write_events():
    client, _ = _login("audit-noop@example.com")
    project_id = client.post("/api/projects", json={"title": "Quiet"}).json()["id"]
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": "S"}).json()
    url = f"/api/projects/{project_id}/sources/{source['id']}/verify"
    client.post(url)
    client.post(url)  # already verified: no second event
    client.post(f"/api/projects/{project_id}/members", json={"email": "nobody@example.com", "role": "reviewer"})  # 404
    client.post(f"/api/projects/{project_id}/sources", json={"title": ""})  # 422

    assert [e[0] for e in _events(project_id)] == ["project.created", "source.created", "source.verified"]


def test_events_never_contain_source_text_or_answers(fake_llm):
    client, _ = _login("audit-secret@example.com")
    project_id = client.post("/api/projects", json={"title": "Private"}).json()["id"]
    client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "SECRET-EXCERPT"})
    client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"})

    dump = repr(_events(project_id))
    assert "SECRET-EXCERPT" not in dump and "Fake answer" not in dump and "test-key" not in dump


def test_research_run_events_record_the_agent_and_model(fake_llm):
    client, user_id = _login("audit-run@example.com")
    project_id = client.post("/api/projects", json={"title": "Runs"}).json()["id"]
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "text"}).json()["id"]
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")  # cited sources must be verified (M1.10.3)
    client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"})

    queued, finished = [e for e in _events(project_id) if e[0].startswith("research_run.")]

    assert queued[0] == "research_run.queued" and queued[1] == user_id
    assert finished[0] == "research_run.finished" and finished[1] == "agent:research-run"
    assert finished[2]["status"] == "completed" and finished[3] == "fake-model"


def test_failed_run_is_recorded_loudly(monkeypatch):
    def fail(self, system, user, schema, max_attempts=None):
        raise LLMResponseError("provider unavailable")

    monkeypatch.setattr(get_settings(), "llm_model", "test-model")
    monkeypatch.setattr(OpenAICompatibleLLM, "complete_json", fail)
    client, _ = _login("audit-fail@example.com")
    project_id = client.post("/api/projects", json={"title": "Fails"}).json()["id"]
    client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "text"})
    client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"})

    finished = [e for e in _events(project_id) if e[0] == "research_run.finished"][0]
    assert finished[2]["status"] == "failed" and finished[3] == "test-model"


def test_arc_ingest_is_recorded_as_unverified(monkeypatch, fake_llm):
    settings = get_settings()
    monkeypatch.setattr(settings, "arc_retrieval_enabled", True)
    monkeypatch.setattr(settings, "arc_retrieval_base_url", "http://arc.test")
    item = ArcRetrievedSource("Found", "https://x.example/1", None, 2020, "scholar", "abs", "loc")
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: [item])
    client, _ = _login("audit-arc@example.com")
    project_id = client.post("/api/projects", json={"title": "Arc"}).json()["id"]
    client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?", "use_web_retrieval": True})

    retrieved = [e for e in _events(project_id) if e[0] == "sources.retrieved"][0]
    assert retrieved[1] == "agent:arc-retrieval"
    assert retrieved[2]["added"] == 1 and retrieved[2]["verified"] is False

