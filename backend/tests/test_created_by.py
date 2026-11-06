import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import executor
from app.agent.arc_client import ArcRetrievedSource
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Source, SourceExcerpt


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "C"}).json()["id"]
    return client, user_id


def test_user_created_rows_record_the_creator_and_expose_it():
    client, user_id = _login("by-user@example.com")
    project_id = client.post("/api/projects", json={"title": "By"}).json()["id"]

    context = client.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"}).json()
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "text"}).json()
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"}).json()

    assert context["created_by"] == source["created_by"] == run["created_by"] == user_id
    with SessionLocal() as db:
        assert db.scalar(select(SourceExcerpt.created_by)) == user_id


def test_a_collaborator_is_recorded_as_the_creator_not_the_owner():
    owner, _ = _login("by-owner@example.com")
    co, co_id = _login("by-co@example.com")
    project_id = owner.post("/api/projects", json={"title": "Shared"}).json()["id"]
    owner.post(f"/api/projects/{project_id}/members", json={"email": "by-co@example.com", "role": "co_author"})

    source = co.post(f"/api/projects/{project_id}/sources", json={"title": "By co-author"}).json()

    assert source["created_by"] == co_id


def test_arc_sources_are_attributed_to_the_agent_not_a_person(monkeypatch, fake_llm):
    settings = get_settings()
    monkeypatch.setattr(settings, "arc_retrieval_enabled", True)
    monkeypatch.setattr(settings, "arc_retrieval_base_url", "http://arc.test")
    item = ArcRetrievedSource("Found", "https://x.example/1", None, 2020, "scholar", "abstract", "loc")
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: [item])
    client, user_id = _login("by-arc@example.com")
    project_id = client.post("/api/projects", json={"title": "Arc"}).json()["id"]

    client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?", "use_web_retrieval": True})

    with SessionLocal() as db:
        source = db.scalar(select(Source).where(Source.title == "Found"))
        assert source.created_by == "agent:arc-retrieval" != user_id
        assert db.scalar(select(SourceExcerpt.created_by).where(SourceExcerpt.source_id == source.id)) == "agent:arc-retrieval"
    listed = client.get(f"/api/projects/{project_id}/sources").json()
    assert listed[0]["created_by"] == "agent:arc-retrieval"


def test_legacy_rows_without_a_creator_still_read_back():
    client, _ = _login("by-legacy@example.com")
    project_id = client.post("/api/projects", json={"title": "Legacy"}).json()["id"]
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "Old"}).json()["id"]
    with SessionLocal() as db:
        source = db.get(Source, uuid.UUID(source_id))
        source.created_by = None
        db.commit()

    assert client.get(f"/api/projects/{project_id}/sources").json()[0]["created_by"] is None
