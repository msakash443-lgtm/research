import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import arc_client, executor
from app.database import SessionLocal
from app.main import app
from app.models import ARC_SOURCE_TYPES, Source


def _project():
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": "origin@example.com", "display_name": "O"}).status_code == 200
    return client, client.post("/api/projects", json={"title": "Origin"}).json()["id"]


def _insert(project_id, **fields):
    with SessionLocal() as db:
        source = Source(project_id=uuid.UUID(project_id), title="S", **fields)
        db.add(source)
        db.commit()
        return str(source.id)


def _read(client, project_id, source_id):
    return next(s for s in client.get(f"/api/projects/{project_id}/sources").json() if s["id"] == source_id)


def test_manual_source_defaults_to_manual_origin():
    client, pid = _project()
    created = client.post(f"/api/projects/{pid}/sources", json={"title": "Mine"}).json()
    assert created["origin"] == "manual" and created["is_automated"] is False


@pytest.mark.parametrize("claimed", ["retrieved", "manual", "anything"])
def test_origin_cannot_be_set_from_a_request(claimed):
    client, pid = _project()
    created = client.post(f"/api/projects/{pid}/sources", json={"title": "Mine", "origin": claimed}).json()
    assert created["origin"] == "manual" and created["is_automated"] is False


def test_is_automated_follows_origin_not_source_type():
    """A connector-saved source with a brand-new type name is still flagged unverified."""
    client, pid = _project()
    connector = _insert(pid, source_type="openalex", origin="retrieved")
    typed_like_arc = _insert(pid, source_type="scholar")  # origin manual: a person's source, not machine-retrieved
    assert _read(client, pid, connector)["is_automated"] is True
    assert _read(client, pid, typed_like_arc)["is_automated"] is False


def test_verifying_clears_the_flag_but_keeps_the_origin():
    client, pid = _project()
    sid = _insert(pid, source_type="openalex", origin="retrieved")
    verified = client.post(f"/api/projects/{pid}/sources/{sid}/verify").json()
    assert verified["is_automated"] is False and verified["origin"] == "retrieved"


def test_sql_expression_matches_python_property():
    client, pid = _project()
    a = _insert(pid, origin="retrieved")
    b = _insert(pid, origin="retrieved", metadata_verified=True)
    c = _insert(pid)
    with SessionLocal() as db:
        flagged = {str(i) for i in db.scalars(select(Source.id).where(Source.is_automated))}
    assert flagged == {a}
    assert b not in flagged and c not in flagged


def test_arc_ingest_marks_sources_retrieved(monkeypatch, fake_llm):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "arc_retrieval_enabled", True)
    monkeypatch.setattr(settings, "arc_retrieval_base_url", "http://arc.test")
    item = arc_client.ArcRetrievedSource("Found", "https://x.example/1", None, 2020, "scholar", "abs", "loc")
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: [item])
    client, pid = _project()

    client.post(f"/api/projects/{pid}/research-runs", json={"question": "A long enough question?", "use_web_retrieval": True})

    rows = client.get(f"/api/projects/{pid}/sources").json()
    assert [(r["title"], r["origin"], r["is_automated"]) for r in rows] == [("Found", "retrieved", True)]


def test_arc_types_stay_reserved_for_manual_input():
    client, pid = _project()
    for source_type in ARC_SOURCE_TYPES:
        assert client.post(f"/api/projects/{pid}/sources", json={"title": "T", "source_type": source_type}).status_code == 422
