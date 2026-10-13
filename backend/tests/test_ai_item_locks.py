"""'Never for this item' (X.46.3): a locked item is never given to an AI step, whatever the caller does."""
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ai_item_locks, suggestion_store
from app.database import SessionLocal
from app.main import app
from app.models import AiSuggestion, AuditEvent, Note, NoteKind, Project, ResearchRun, ScreeningDecision
from app.prescreen import run_prescreen
from tests.test_prescreen import ScriptedLLM, answer


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"lock-{tag}@example.com", "display_name": "L"})
    pid = owner.post("/api/projects", json={"title": "Locks"}).json()["id"]
    owner.put(
        f"/api/projects/{pid}/criteria",
        json={"framework": "custom", "criteria": [{"kind": "include", "text": "Empirical studies"}, {"kind": "exclude", "text": "Opinion pieces"}]},
    )
    ids = [owner.post(f"/api/projects/{pid}/sources", json={"title": f"Paper {n}"}).json()["id"] for n in range(3)]
    return {"owner": owner, "pid": pid, "ids": ids}


def _lock(world, kind, target_id):
    return world["owner"].put(f"/api/projects/{world['pid']}/ai-locks/{kind}/{target_id}")


def _prescreen(world, replies):
    llm = ScriptedLLM(replies)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        result = run_prescreen(db, project, llm=llm, min_confidence=0.6, actor="tester")
    return result, llm


def _actions(world):
    with SessionLocal() as db:
        return [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world["pid"])).order_by(AuditEvent.timestamp))]


def test_locking_is_idempotent_listed_and_audited_and_unlocking_is_audited(world):
    target = world["ids"][0]
    first, second = _lock(world, "source", target), _lock(world, "source", target)
    assert first.json()["changed"] is True and second.json()["changed"] is False
    listed = world["owner"].get(f"/api/projects/{world['pid']}/ai-locks").json()
    assert [(x["target_kind"], x["target_id"]) for x in listed] == [("source", target)]
    assert _actions(world).count("ai_lock.set") == 1

    cleared = world["owner"].delete(f"/api/projects/{world['pid']}/ai-locks/source/{target}").json()
    assert cleared["locked"] is False and cleared["changed"] is True
    assert world["owner"].get(f"/api/projects/{world['pid']}/ai-locks").json() == []
    assert _actions(world).count("ai_lock.cleared") == 1


def test_a_locked_source_is_never_sent_to_pre_screening_and_the_others_still_are(world):
    _lock(world, "source", world["ids"][0])

    result, llm = _prescreen(world, [answer("include", "I1"), answer("include", "I1"), answer("include", "I1")])

    assert (result.suggested, result.locked) == (2, 1)
    assert len(llm.prompts) == 2 and not any("Paper 0" in user for _, user in llm.prompts)
    with SessionLocal() as db:
        decided = {r.source_id for r in db.scalars(select(ScreeningDecision))}
    assert uuid.UUID(world["ids"][0]) not in decided


def test_unlocking_lets_pre_screening_see_the_item_again(world):
    _lock(world, "source", world["ids"][0])
    world["owner"].delete(f"/api/projects/{world['pid']}/ai-locks/source/{world['ids'][0]}")
    result, _ = _prescreen(world, [answer("include", "I1")] * 3)
    assert (result.suggested, result.locked) == (3, 0)


def test_locking_a_screening_target_locks_the_source_for_every_ai_step(world):
    body = _lock(world, "screening", world["ids"][1]).json()
    assert body["target_kind"] == "source"
    result, llm = _prescreen(world, [answer("include", "I1")] * 3)
    assert result.locked == 1 and not any("Paper 1" in user for _, user in llm.prompts)


def test_a_research_run_leaves_out_a_locked_source_and_a_locked_context_item(world, fake_llm):
    owner = world["owner"]
    pid = owner.post("/api/projects", json={"title": "Run locks"}).json()["id"]  # no unverified sources, so [S1] is citable
    world = {**world, "pid": pid}
    kept = owner.post(f"/api/projects/{pid}/sources", json={"title": "Alpha release", "evidence_excerpt": "Output rose.", "locator": "p1"}).json()
    hidden = owner.post(f"/api/projects/{pid}/sources", json={"title": "Bravo secret", "evidence_excerpt": "Private finding.", "locator": "p2"}).json()
    for source in (kept, hidden):
        owner.post(f"/api/projects/{pid}/sources/{source['id']}/verify")
    owner.post(f"/api/projects/{pid}/context", json={"kind": "question", "content": "Did output rise?"})
    secret = owner.post(f"/api/projects/{pid}/context", json={"kind": "hypothesis", "content": "Confidential hypothesis text"}).json()
    assert _lock(world, "source", hidden["id"]).status_code == 200
    assert _lock(world, "context_item", secret["id"]).status_code == 200

    run = owner.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output rise at all?"}).json()

    assert run["status"] == "completed"
    sent = " ".join(request.content.decode() for request in fake_llm.requests)
    assert "Alpha release" in sent and "Output rose." in sent
    assert "Bravo secret" not in sent and "Private finding" not in sent and "Confidential hypothesis" not in sent
    with SessionLocal() as db:
        snapshot = db.get(ResearchRun, uuid.UUID(run["id"])).input_snapshot
    assert [s["title"] for s in snapshot["sources"]] == ["Alpha release"]
    assert [c["content"] for c in snapshot["context"]] == ["Did output rise?"]
    listed = {c["id"]: c for c in owner.get(f"/api/projects/{pid}/context").json()}
    assert listed[secret["id"]]["ai_locked"] is True and listed[secret["id"]]["used_by_agent"] is False


def test_a_suggestion_cannot_be_stored_about_a_locked_item_and_locking_closes_the_open_ones(world):
    target = world["ids"][0]
    with SessionLocal() as db:
        pending = suggestion_store.create_suggestion(
            db, project_id=uuid.UUID(world["pid"]), target_kind="screening", target_id=target, content="Include: fits.",
            model_id="m", prompt_version="p@1", producing_agent="prescreen", confidence=0.9,
        )
        db.commit()
        suggestion_id = pending.id

    assert _lock(world, "source", target).json()["suggestions_locked"] == 1

    with SessionLocal() as db:
        assert suggestion_store.state_of(db.get(AiSuggestion, suggestion_id)) == "locked"
        with pytest.raises(ai_item_locks.AiItemLocked):
            suggestion_store.create_suggestion(
                db, project_id=uuid.UUID(world["pid"]), target_kind="screening", target_id=target, content="Again",
                model_id="m", prompt_version="p@1", producing_agent="prescreen",
            )
    # a person can no longer accept what is now "never for this item"
    assert world["owner"].post(f"/api/projects/{world['pid']}/suggestions/{suggestion_id}/accept").status_code == 409


def test_a_note_set_to_ai_locked_is_locked_through_the_same_check(world):
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        owner_id = db.scalar(select(Project.owner_id).where(Project.id == uuid.UUID(world["pid"])))
        note = Note(owner_id=owner_id, kind=NoteKind.typed, body="private thought", client_id="c-1", ai_locked=True, captured_at=now)
        free = Note(owner_id=owner_id, kind=NoteKind.typed, body="fine", client_id="c-2", ai_locked=False, captured_at=now)
        db.add_all([note, free])
        db.commit()
        pid = uuid.UUID(world["pid"])
        assert ai_item_locks.is_locked(db, pid, "note", note.id) is True
        assert ai_item_locks.is_locked(db, pid, "note", free.id) is False
        with pytest.raises(ai_item_locks.AiItemLocked):
            ai_item_locks.ensure_not_locked(db, pid, "note", note.id)


def test_what_cannot_be_locked_is_refused_and_a_foreign_item_is_not_found(world):
    pid = world["pid"]
    assert _lock(world, "gate", "G1").status_code == 422
    assert _lock(world, "note", str(uuid.uuid4())).status_code == 422
    assert _lock(world, "source", str(uuid.uuid4())).status_code == 404
    other = TestClient(app)
    other.post("/api/auth/development/login", json={"email": f"lock-other-{uuid.uuid4().hex[:6]}@example.com", "display_name": "O"})
    foreign = other.post("/api/projects", json={"title": "Elsewhere"}).json()["id"]
    foreign_source = other.post(f"/api/projects/{foreign}/sources", json={"title": "Theirs"}).json()["id"]
    assert _lock(world, "source", foreign_source).status_code == 404  # not in this project
    assert world["owner"].get(f"/api/projects/{pid}/ai-locks").json() == []
