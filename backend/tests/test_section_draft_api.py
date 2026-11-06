"""Section drafting (X.46.8 / M6.1.a): refusals, the queued task, and the handler's stored suggestion."""

import json
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai_item_locks import set_lock
from app.database import SessionLocal
from app.main import app
from app.models import AiSuggestion, AuditEvent, Document, Task
from app.task_handlers import SECTION_DRAFT
from app.task_registry import get_handler


def _project(email, *, ai="suggestions_only"):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "U"}).status_code == 200
    pid = client.post("/api/projects", json={"title": "Section Draft"}).json()["id"]
    if ai != "suggestions_only":
        assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": ai}).status_code == 200
    return client, pid


def _document(client, pid, title="Introduction"):
    return client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": 0}).json()["id"]


def _claim(client, pid, doc_id, text="Trust predicts collaboration."):
    response = client.post(f"/api/projects/{pid}/claims", json={"text": text, "section_id": doc_id})
    assert response.status_code == 201
    return response.json()["id"]


def _request(client, pid, doc_id):
    return client.post(f"/api/projects/{pid}/documents/{doc_id}/draft-suggestion")


def _reply(fake_llm, payload):
    fake_llm.handler = lambda request: {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _run_task(payload):
    handler = get_handler(SECTION_DRAFT)
    handler(SimpleNamespace(payload=payload, ensure_owned=lambda: None))


def _suggestions(pid):
    with SessionLocal() as db:
        return db.scalars(select(AiSuggestion).where(AiSuggestion.project_id == uuid.UUID(pid)).order_by(AiSuggestion.created_at)).all()


def _audit(pid, action):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == action)).all()
        return [dict(r.payload_json or {}) for r in rows]


def test_with_ai_off_the_request_is_refused_and_nothing_is_queued(fake_llm):
    client, pid = _project("sd-off@example.com", ai="off")
    doc = _document(client, pid)
    response = _request(client, pid, doc)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_off"
    assert fake_llm.requests == []


def test_a_locked_section_is_never_sent(fake_llm):
    client, pid = _project("sd-lock@example.com")
    doc = _document(client, pid)
    with SessionLocal() as db:
        set_lock(db, uuid.UUID(pid), "document", doc, "user:test")
        db.commit()
    response = _request(client, pid, doc)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_locked"
    assert fake_llm.requests == []


def test_a_request_is_allowed_at_suggestions_only_level(fake_llm):
    client, pid = _project("sd-level@example.com", ai="suggestions_only")
    doc = _document(client, pid)
    response = _request(client, pid, doc)
    assert response.status_code == 202


def test_a_valid_request_queues_one_task_and_audits_it(fake_llm):
    client, pid = _project("sd-queue@example.com")
    doc = _document(client, pid)
    response = _request(client, pid, doc)
    assert response.status_code == 202
    task_id = uuid.UUID(response.json()["task_id"])
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.type == SECTION_DRAFT and task.status.value == "queued"
    [requested] = _audit(pid, "ai.requested")
    assert requested["action"] == "draft" and requested["target_kind"] == "document" and requested["target_id"] == doc
    assert fake_llm.requests == []  # queued, not yet sent


def test_the_handler_refuses_loudly_when_the_section_has_no_claims(fake_llm):
    client, pid = _project("sd-empty@example.com")
    doc = _document(client, pid)
    from app.task_registry import PermanentTaskError
    import pytest

    with pytest.raises(PermanentTaskError):
        _run_task({"project_id": pid, "document_id": doc, "actor": "user:test"})
    assert _suggestions(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "empty_outline"
    assert fake_llm.requests == []


def test_the_handler_stores_one_suggestion_built_from_the_claim_outline(fake_llm):
    client, pid = _project("sd-handler@example.com")
    doc = _document(client, pid)
    _claim(client, pid, doc, text="Trust predicts collaboration.")
    _claim(client, pid, doc, text="The effect held across three samples.")
    _reply(fake_llm, {"draft": "Trust predicts collaboration, and this effect held across three samples."})

    _run_task({"project_id": pid, "document_id": doc, "actor": "user:test"})

    [row] = _suggestions(pid)
    assert row.content == "Trust predicts collaboration, and this effect held across three samples."
    assert row.action == "draft" and row.target_kind == "document" and row.target_id == doc
    assert row.target_field == "draft"
    assert row.model_id == "fake-model" and row.prompt_version == "section_draft@1"
    [sent] = fake_llm.requests
    body = json.loads(sent.content)
    user_message = body["messages"][-1]["content"]
    assert "CLAIM 1" in user_message and "Trust predicts collaboration." in user_message
    assert "CLAIM 2" in user_message and "three samples" in user_message
    assert len(_audit(pid, "ai.proposed")) == 1


def test_a_reply_that_repeats_the_prompt_is_refused_and_nothing_is_stored(fake_llm):
    client, pid = _project("sd-leak@example.com")
    doc = _document(client, pid)
    _claim(client, pid, doc)
    from app.answer_guard import _SENTENCE_SPLIT
    from app.prompt_registry import load_prompt
    from app.task_registry import PermanentTaskError
    import pytest

    prompt = load_prompt("section_draft", 1)
    leaked_sentence = next(s for s in _SENTENCE_SPLIT.split(prompt.system) if len(s) >= 40)
    _reply(fake_llm, {"draft": leaked_sentence})

    with pytest.raises(PermanentTaskError):
        _run_task({"project_id": pid, "document_id": doc, "actor": "user:test"})
    assert _suggestions(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "leaked_prompt"


def test_a_stored_draft_is_decided_through_the_shared_suggestion_endpoints(fake_llm):
    client, pid = _project("sd-accept@example.com")
    doc = _document(client, pid)
    _claim(client, pid, doc)
    _reply(fake_llm, {"draft": "A drafted paragraph from the outline."})
    _run_task({"project_id": pid, "document_id": doc, "actor": "user:test"})
    [row] = _suggestions(pid)

    listed = client.get(f"/api/projects/{pid}/suggestions?target_kind=document&target_id={doc}").json()
    assert any(item["id"] == str(row.id) and item["action"] == "draft" for item in listed)

    accepted = client.post(f"/api/projects/{pid}/suggestions/{row.id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "accepted"
    assert accepted.json()["accepted_text"] == "A drafted paragraph from the outline."

    undone = client.post(f"/api/projects/{pid}/suggestions/{row.id}/undo")
    assert undone.status_code == 200 and undone.json()["state"] == "pending"

    discarded = client.post(f"/api/projects/{pid}/suggestions/{row.id}/discard")
    assert discarded.status_code == 200 and discarded.json()["state"] == "discarded"

    restored = client.post(f"/api/projects/{pid}/suggestions/{row.id}/restore")
    assert restored.status_code == 200 and restored.json()["state"] == "pending"

    edited = client.post(f"/api/projects/{pid}/suggestions/{row.id}/edit-accept", json={"text": "My own rewording."})
    assert edited.status_code == 200
    assert edited.json()["state"] == "accepted" and edited.json()["accepted_text"] == "My own rewording."


def _accepted_draft(client, pid, doc, fake_llm, text="A drafted paragraph from the outline."):
    _claim(client, pid, doc)
    _reply(fake_llm, {"draft": text})
    _run_task({"project_id": pid, "document_id": doc, "actor": "user:test"})
    [row] = _suggestions(pid)
    assert client.post(f"/api/projects/{pid}/suggestions/{row.id}/accept").status_code == 200
    return str(row.id)


def _save(client, pid, doc, runs, base_number):
    user_id = client.get("/api/auth/me").json()["id"]
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": text, "marks": [{"type": "prov", "attrs": {
            "sid": sid, "origin": origin, "by": user_id, "sug": sug, "note": None, "paste": None, "src": None}}]}
        for text, sid, origin, sug in runs]}]}
    return client.post(f"/api/projects/{pid}/documents/{doc}/versions", json={
        "base_number": base_number, "client_save_id": uuid.uuid4().hex, "client_session_id": "s", "content_json": content})


def test_at_suggestions_only_an_accepted_draft_cannot_be_saved_into_the_section(fake_llm):
    client, pid = _project("sd-q27-refused@example.com")
    doc = _document(client, pid)
    sug = _accepted_draft(client, pid, doc, fake_llm)

    response = _save(client, pid, doc, [("A drafted paragraph from the outline.", "d" * 8, "ai_suggested", sug)], 0)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ai_wording_not_allowed"
    with SessionLocal() as db:
        assert db.get(Document, uuid.UUID(doc)).current_version_id is None


def test_at_full_an_accepted_draft_is_saved_and_stays_saveable_after_the_level_is_lowered(fake_llm):
    client, pid = _project("sd-q27-full@example.com", ai="full")
    doc = _document(client, pid)
    sug = _accepted_draft(client, pid, doc, fake_llm)
    draft = ("A drafted paragraph from the outline.", "d" * 8, "ai_suggested", sug)

    assert _save(client, pid, doc, [draft], 0).status_code == 201

    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "suggestions_only"}).status_code == 200
    kept = _save(client, pid, doc, [draft, (" My own sentence.", "t" * 8, "typed", None)], 1)
    assert kept.status_code == 201, kept.text
