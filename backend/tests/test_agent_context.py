import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.agent.context import MAX_CONTEXT_ITEMS, select_agent_context
from app.agent.llm import OpenAICompatibleLLM
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import ContextKind, ResearchContextItem
from tests.llm_replies import structured

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _item(minutes: int, kind: ContextKind, content: str) -> ResearchContextItem:
    return ResearchContextItem(id=uuid.uuid4(), kind=kind, content=content, created_at=START + timedelta(minutes=minutes))


def _over_cap_items() -> list[ResearchContextItem]:
    # The question and objective are the two oldest; 24 newer decisions follow (26 > 20).
    items = [_item(0, ContextKind.question, "question"), _item(1, ContextKind.objective, "objective")]
    items += [_item(2 + n, ContextKind.decision, f"decision {n}") for n in range(24)]
    return items


def test_core_items_are_kept_and_the_rest_are_newest():
    items = _over_cap_items()

    chosen = [item.content for item in select_agent_context(reversed(items))]

    assert len(chosen) == MAX_CONTEXT_ITEMS
    # Oldest first; the 18 remaining slots go to the newest decisions (6..23), so 0..5 drop out.
    assert chosen == ["question", "objective"] + [f"decision {n}" for n in range(6, 24)]


def test_under_the_cap_everything_is_sent_oldest_first():
    items = [_item(2, ContextKind.methodology, "c"), _item(0, ContextKind.decision, "a"), _item(1, ContextKind.question, "b")]

    assert [item.content for item in select_agent_context(items)] == ["a", "b", "c"]


def test_more_core_items_than_the_cap_keeps_the_newest_core():
    items = [_item(n, ContextKind.question, f"q{n}") for n in range(5)] + [_item(10, ContextKind.decision, "d")]

    assert [item.content for item in select_agent_context(items, limit=3)] == ["q2", "q3", "q4"]


def test_ideas_never_take_a_slot_or_reach_the_agent():
    # 20 decisions fill the cap exactly; a newer idea must still never appear.
    items = [_item(n, ContextKind.decision, f"decision {n}") for n in range(20)]
    items.append(_item(30, ContextKind.idea, "a newer idea"))

    chosen = [item.content for item in select_agent_context(items)]

    assert chosen == [f"decision {n}" for n in range(20)]
    assert "a newer idea" not in chosen


def _project_with_over_cap_context(client) -> str:
    assert client.post("/api/auth/development/login", json={"email": "ctx@example.com", "display_name": "C"}).status_code == 200
    project_id = client.post("/api/projects", json={"title": "Context cap"}).json()["id"]
    db = SessionLocal()
    for item in _over_cap_items():
        item.project_id = uuid.UUID(project_id)
        db.add(item)
    db.commit()
    db.close()
    return project_id


def test_context_list_marks_items_left_out_of_runs():
    client = TestClient(app)
    project_id = _project_with_over_cap_context(client)

    listed = client.get(f"/api/projects/{project_id}/context").json()

    assert len(listed) == 26
    left_out = [item["content"] for item in listed if item["used_by_agent"] is False]
    assert left_out == [f"decision {n}" for n in range(6)]
    assert sum(item["used_by_agent"] is True for item in listed) == MAX_CONTEXT_ITEMS


def test_run_snapshot_keeps_the_research_question(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_model", "test-model")
    prompts = []
    monkeypatch.setattr(OpenAICompatibleLLM, "complete_json", lambda self, system, user, schema, max_attempts=None: prompts.append(user) or structured("Answer [S1]."))
    client = TestClient(app)
    project_id = _project_with_over_cap_context(client)
    added = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Official release", "evidence_excerpt": "Participation rose.", "locator": "p. 1"},
    )
    assert added.status_code == 201
    client.post(f"/api/projects/{project_id}/sources/{added.json()['id']}/verify")  # cited sources must be verified (M1.10.3)

    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"}).json()

    assert run["status"] == "completed"
    snapshot = [item["content"] for item in run["input_snapshot"]["context"]]
    assert snapshot == ["question", "objective"] + [f"decision {n}" for n in range(6, 24)]
    # The snapshot matches what the model was actually given (rule 25).
    assert "decision 5" not in prompts[0] and "decision 6" in prompts[0] and "objective" in prompts[0]
    listed = client.get(f"/api/projects/{project_id}/context").json()
    assert [item["content"] for item in listed if item["used_by_agent"]] == snapshot


def test_a_saved_idea_is_listed_but_never_sent_in_a_run(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_model", "test-model")
    prompts = []
    monkeypatch.setattr(OpenAICompatibleLLM, "complete_json", lambda self, system, user, schema, max_attempts=None: prompts.append(user) or structured("Answer [S1]."))
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": "idea@example.com", "display_name": "I"}).status_code == 200
    project_id = client.post("/api/projects", json={"title": "Idea capture"}).json()["id"]
    client.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "What changed?"})
    idea = client.post(
        f"/api/projects/{project_id}/context",
        json={"kind": "idea", "content": "IDEA-CANARY maybe compare districts"},
    )
    assert idea.status_code == 201
    assert idea.json()["kind"] == "idea"
    added = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Official release", "evidence_excerpt": "Participation rose.", "locator": "p. 1"},
    )
    assert added.status_code == 201
    client.post(f"/api/projects/{project_id}/sources/{added.json()['id']}/verify")

    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"}).json()

    assert run["status"] == "completed"
    assert "IDEA-CANARY" not in prompts[0]
    assert all("IDEA-CANARY" not in item["content"] for item in run["input_snapshot"]["context"])
    listed = client.get(f"/api/projects/{project_id}/context").json()
    idea_row = next(item for item in listed if item["kind"] == "idea")
    assert idea_row["used_by_agent"] is False
