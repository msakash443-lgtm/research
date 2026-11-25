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
    # X.224: a screening suggestion is only visible to a person who has decided that source themselves
    assert world["owner"].post(f"/api/projects/{world['pid']}/screening/decisions", json={"source_id": target, "decision": "include"}).status_code == 201
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


def test_a_lock_set_while_pre_screening_runs_stops_the_next_item_from_being_sent(world):
    # The lock lands in the middle of the run (after the first model call, from another request). The second
    # source was undecided and unlocked when the run listed its work, so only the per-item recheck before its
    # model call can keep it from being sent.
    owner = world["owner"]
    pid = world["pid"]
    locked_target = world["ids"][1]

    class LockingLLM(ScriptedLLM):
        def complete(self, system_prompt, user_prompt):
            if not self.prompts:
                assert owner.put(f"/api/projects/{pid}/ai-locks/source/{locked_target}").status_code == 200
            return super().complete(system_prompt, user_prompt)

    llm = LockingLLM([answer("include", "I1"), answer("include", "I1"), answer("include", "I1")])
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        result = run_prescreen(db, project, llm=llm, min_confidence=0.6, actor="tester")

    assert (result.suggested, result.locked) == (2, 1)
    assert len(llm.prompts) == 2 and not any("Paper 1" in user for _, user in llm.prompts)
    with SessionLocal() as db:
        decided = {r.source_id for r in db.scalars(select(ScreeningDecision))}
    assert uuid.UUID(locked_target) not in decided


def test_front_matter_for_a_locked_section_is_refused_and_nothing_is_sent(fake_llm):
    from types import SimpleNamespace

    from app import front_matter_task
    from app.models import Document
    from app.task_registry import PermanentTaskError
    from tests.test_front_matter_integration import _task, _world

    client, pid, doc, version, _ = _world()
    with SessionLocal() as db:
        db.get(Document, uuid.UUID(doc)).ai_locked = True
        db.commit()

    refused = client.post(f"/api/projects/{pid}/documents/{doc}/front-matter", json={"kind": "abstract"})

    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ai_locked"
    # A request queued before the lock was set still stops at the worker.
    with pytest.raises(PermanentTaskError, match="ai_locked"):
        front_matter_task.run_front_matter(_task(pid, doc, version), _task(pid, doc, version).payload)
    assert fake_llm.requests == []


def test_ai_extraction_of_a_locked_source_is_refused_by_the_route_and_the_worker(fake_llm):
    from app.models import Source, Task, TaskStatus
    from app.object_storage import fulltext_key
    from app.task_runner import run_one_task
    from tests.api_helpers import create_included_source, create_project, sign_in

    client = sign_in(f"lock-extract-{uuid.uuid4().hex}@example.com")
    pid = create_project(client, "Locked extraction")
    source_id = create_included_source(client, pid, "Secret paper")
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source_id)).fulltext_path = fulltext_key(uuid.UUID(pid), uuid.UUID(source_id), "pdf")
        db.commit()
    body = {"source_id": source_id, "schema_name": "default"}
    queued = client.post(f"/api/projects/{pid}/extractions/ai", json=body)
    assert queued.status_code == 202, queued.text

    assert client.put(f"/api/projects/{pid}/ai-locks/source/{source_id}").status_code == 200
    refused = client.post(f"/api/projects/{pid}/extractions/ai", json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ai_locked"
    assert run_one_task(task_id=uuid.UUID(queued.json()["task_id"]))

    with SessionLocal() as db:
        task = db.get(Task, uuid.UUID(queued.json()["task_id"]))
        assert task.status == TaskStatus.failed and "never send to AI" in task.error
    assert fake_llm.requests == []


def test_auto_extraction_reports_a_locked_source_as_ai_locked_and_queues_nothing_for_it():
    from app.models import Task
    from tests.api_helpers import create_included_source, create_project, sign_in

    client = sign_in(f"lock-auto-{uuid.uuid4().hex}@example.com")
    pid = create_project(client, "Locked auto extraction")
    locked = create_included_source(client, pid, "Secret paper", doi="10.5555/locked")
    open_one = create_included_source(client, pid, "Open paper", doi="10.5555/open")
    assert client.put(f"/api/projects/{pid}/ai-locks/source/{locked}").status_code == 200

    response = client.post(f"/api/projects/{pid}/extractions/ai/auto", json={"schema_name": "default"})

    assert response.status_code == 202, response.text
    outcomes = {o["source_id"]: o for o in response.json()["outcomes"]}
    assert outcomes[locked]["status"] == "ai_locked" and outcomes[locked]["task_id"] is None
    assert outcomes[open_one]["status"] == "fetch_queued"
    with SessionLocal() as db:
        queued = db.scalars(select(Task).where(Task.project_id == uuid.UUID(pid))).all()
    assert all((t.payload or {}).get("source_id") != locked for t in queued)


def test_clustering_never_embeds_a_locked_source(world):
    from app.thematic_clusters import run_clustering

    assert _lock(world, "source", world["ids"][1]).status_code == 200

    class Embedder:
        model = "test-embedding"

        def __init__(self):
            self.texts = []

        def embed(self, texts):
            self.texts.extend(texts)
            return [[1.0, 0.0] if index == 0 else [0.0, 1.0] for index, _ in enumerate(texts)]

    embedder = Embedder()
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        run = run_clustering(db, project, embedder=embedder, k=2, seed=0, actor="tester", batch_size=64, max_sources=100)
        db.commit()
        assert run.n_sources == 2
    assert len(embedder.texts) == 2 and not any("Paper 1" in text for text in embedder.texts)


def test_semantic_novelty_skips_locked_ideas_and_sources(world):
    from app.routers.idea_brief import get_embedder

    owner, pid = world["owner"], world["pid"]
    kept = owner.post(f"/api/projects/{pid}/context", json={"kind": "idea", "content": "Open idea"}).json()
    secret = owner.post(f"/api/projects/{pid}/context", json={"kind": "idea", "content": "Private idea"}).json()
    assert _lock(world, "context_item", secret["id"]).status_code == 200
    assert _lock(world, "source", world["ids"][1]).status_code == 200
    seen = []

    class Embedder:
        model = "test-embed"

        def embed(self, texts):
            seen.extend(texts)
            return [[1.0, 1.0 + index] for index, _ in enumerate(texts)]

    app.dependency_overrides[get_embedder] = lambda: Embedder()
    try:
        body = owner.get(f"/api/projects/{pid}/idea-brief/semantic-novelty").json()
    finally:
        app.dependency_overrides.pop(get_embedder, None)

    assert not any("Private idea" in text or "Paper 1" in text for text in seen)
    scored = {i["id"]: i for i in body["ideas"]}
    assert scored[secret["id"]]["distinctiveness"] is None and scored[secret["id"]]["comparators"] == []
    assert scored[kept["id"]]["distinctiveness"] is not None
    assert all(c["title"] != "Paper 1" for c in scored[kept["id"]]["comparators"])


def test_gap_suggestions_never_send_a_locked_source(fake_llm):
    import json

    from app.models import Extraction
    from tests.api_helpers import create_project, insert_source, sign_in

    client = sign_in(f"lock-gaps-{uuid.uuid4().hex}@example.com")
    pid = create_project(client, "Locked gaps")
    ids = []
    for title, method, population in (("Open A", "survey", "teachers"), ("Open B", "interview", "nurses"), ("Hidden Z", "survey", "nurses")):
        sid = insert_source(pid, title=title, year=2020, abstract=f"{title} abstract", metadata_verified=True)
        ids.append(sid)
        with SessionLocal() as db:
            db.add(Extraction(project_id=uuid.UUID(pid), source_id=uuid.UUID(sid), schema_version="default@1",
                              fields_json={"method": {"design": method}, "sample": {"population": population, "country": "UK"}},
                              evidence_spans={}, extracted_by="human", extractor="test"))
            db.commit()
    assert client.put(f"/api/projects/{pid}/ai-locks/source/{ids[2]}").status_code == 200
    fake_llm.handler = lambda r: {"choices": [{"message": {"content": json.dumps({"gaps": []})}}]}

    response = client.post(f"/api/projects/{pid}/gaps/suggest")

    assert response.status_code == 200, response.text
    sent = fake_llm.requests[0].content.decode()
    assert "Open A" in sent and "Hidden Z" not in sent and ids[2] not in sent
