"""X.116: a research run builds each source's snapshot exactly once, so the excerpt budget is applied to one copy."""

import uuid

from fastapi.testclient import TestClient

from app.agent import executor
from app.database import SessionLocal
from app.main import app
from app.models import ResearchRun
from tests.llm_replies import chat_reply


def test_each_source_snapshot_is_built_once_per_run(fake_llm, monkeypatch):
    fake_llm.handler = lambda request: chat_reply("Wages fell [S1].")
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "snapshot-x116@example.com", "display_name": "S"})
    pid = client.post("/api/projects", json={"title": "Snapshot"}).json()["id"]
    for title in ("First report", "Second report"):
        sid = client.post(
            f"/api/projects/{pid}/sources",
            json={"title": title, "evidence_excerpt": f"{title} says wages rose in 2020.", "locator": "p. 1"},
        ).json()["id"]
        client.post(f"/api/projects/{pid}/sources/{sid}/verify")

    built = []
    original = executor._source_snapshot

    def counting(source):
        built.append(source.id)
        return original(source)

    monkeypatch.setattr(executor, "_source_snapshot", counting)
    response = client.post(f"/api/projects/{pid}/research-runs", json={"question": "What changed in wages?"})

    assert response.status_code == 202
    with SessionLocal() as db:
        run = db.get(ResearchRun, uuid.UUID(response.json()["id"]))
        snapshot_titles = sorted(source["title"] for source in run.input_snapshot["sources"])
    assert len(built) == 2 and len(set(built)) == 2
    assert snapshot_titles == ["First report", "Second report"]
