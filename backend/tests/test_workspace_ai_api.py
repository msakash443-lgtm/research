"""Workspace AI side panel (X.31.11): refusals, the queued task, the preview, and the handler's stored suggestions."""

import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AiSuggestion, AuditEvent, Task
from app.task_handlers import WORKSPACE_AI_ACTION
from app.task_registry import PermanentTaskError, get_handler


def _project(email, *, ai="suggestions_only"):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "U"}).status_code == 200
    pid = client.post("/api/projects", json={"title": "Workspace AI"}).json()["id"]
    if ai != "suggestions_only":
        assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": ai}).status_code == 200
    return client, pid


def _document(client, pid, title="Introduction"):
    return client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": 0}).json()["id"]


def _source_with_excerpt(client, pid, title="Trial A", text="Output rose by 12 percent.", verified=True):
    sid = client.post(f"/api/projects/{pid}/sources", json={"title": title, "evidence_excerpt": text, "locator": "p1"}).json()["id"]
    if verified:
        client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    return sid


def _excerpt_id(source_id):
    from app.models import SourceExcerpt

    with SessionLocal() as db:
        return str(db.scalars(select(SourceExcerpt.id).where(SourceExcerpt.source_id == uuid.UUID(source_id))).first())


def _request(client, pid, **overrides):
    body = {"action": "challenge", "text": "Output rose in every country studied.", "document_id": None}
    body.update(overrides)
    return client.post(f"/api/projects/{pid}/ai/suggestions", json=body)


def _reply(fake_llm, payload):
    fake_llm.handler = lambda request: {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _run_task(payload):
    handler = get_handler(WORKSPACE_AI_ACTION)
    handler(SimpleNamespace(payload=payload, ensure_owned=lambda: None))


def _suggestions(pid):
    with SessionLocal() as db:
        return db.scalars(select(AiSuggestion).where(AiSuggestion.project_id == uuid.UUID(pid)).order_by(AiSuggestion.created_at)).all()


def _audit(pid, action):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == action)).all()
        return [dict(r.payload_json or {}) for r in rows]


def test_with_ai_off_nothing_is_queued_and_the_code_says_why(fake_llm):
    client, pid = _project("wai-off@example.com", ai="off")
    doc = _document(client, pid)
    response = _request(client, pid, document_id=doc)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_off"
    assert fake_llm.requests == []


def test_clearer_sentence_needs_the_full_level(fake_llm):
    client, pid = _project("wai-level@example.com")
    doc = _document(client, pid)
    response = _request(client, pid, action="clearer_sentence", document_id=doc)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_level_too_low"


def test_a_locked_document_is_never_sent(fake_llm):
    client, pid = _project("wai-lock@example.com")
    doc = _document(client, pid)
    assert client.patch(f"/api/projects/{pid}/documents/{doc}", json={"ai_locked": True}).status_code == 200
    response = _request(client, pid, document_id=doc)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_locked"
    assert fake_llm.requests == []


def test_text_must_be_present_and_capped(fake_llm):
    client, pid = _project("wai-text@example.com")
    doc = _document(client, pid)
    assert _request(client, pid, text="   ", document_id=doc).status_code == 422
    assert _request(client, pid, text="x" * 2001, document_id=doc).status_code == 422


def test_exactly_one_target_is_required(fake_llm):
    client, pid = _project("wai-target@example.com")
    assert _request(client, pid).status_code == 422
    doc = _document(client, pid)
    note = str(uuid.uuid4())
    assert _request(client, pid, document_id=doc, note_id=note).status_code == 422


def test_find_evidence_refuses_a_project_with_no_sources(fake_llm):
    client, pid = _project("wai-nosrc@example.com")
    doc = _document(client, pid)
    response = _request(client, pid, action="find_evidence", document_id=doc)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_sources"


def test_a_valid_request_queues_one_task_and_audits_without_the_text(fake_llm):
    client, pid = _project("wai-queue@example.com")
    doc = _document(client, pid)
    response = _request(client, pid, document_id=doc)
    assert response.status_code == 202
    task_id = uuid.UUID(response.json()["task_id"])
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.type == WORKSPACE_AI_ACTION and task.status.value == "queued"
    [requested] = _audit(pid, "ai.requested")
    assert requested["action"] == "challenge" and requested["input_chars"] == len("Output rose in every country studied.")
    assert "Output rose" not in json.dumps(requested)
    assert fake_llm.requests == []  # queued, not yet sent


def test_preview_says_where_the_text_would_go_and_sends_nothing(fake_llm):
    client, pid = _project("wai-preview@example.com")
    doc = _document(client, pid)
    before = len(_suggestions(pid))
    response = client.post(f"/api/projects/{pid}/ai/suggestions/preview", json={"action": "challenge", "text": "One two three.", "document_id": doc})
    assert response.status_code == 200
    body = response.json()
    assert body["words"] == 3 and body["model_route"] == "standard" and body["sends_to"] == "the project's standard model"
    assert fake_llm.requests == []
    assert len(_suggestions(pid)) == before


def test_the_handler_stores_one_suggestion_per_comment_with_the_record_of_what_was_sent(fake_llm):
    client, pid = _project("wai-handler@example.com")
    doc = _document(client, pid)
    text = "Output rose in every country studied."
    _reply(fake_llm, {"items": [{"text": "Which countries, and how many?"}, {"text": "Does this hold outside the trial?"}]})
    _run_task({"project_id": pid, "action": "challenge", "text": text, "document_id": doc, "actor": "user:test"})

    rows = _suggestions(pid)
    assert [r.content for r in rows] == ["Which countries, and how many?", "Does this hold outside the trial?"]
    for row in rows:
        assert row.action == "challenge" and row.target_kind == "document" and row.target_id == doc
        assert row.input_hash == hashlib.sha256(text.encode()).hexdigest() and row.input_chars == len(text)
        assert row.model_id == "fake-model" and row.prompt_version == "workspace_challenge@1"
    [sent] = fake_llm.requests
    body = json.loads(sent.content)
    assert "SELECTED TEXT" in body["messages"][-1]["content"] and text in body["messages"][-1]["content"]
    assert len(_audit(pid, "ai.proposed")) == 1


def test_a_reply_naming_an_unsent_source_stores_nothing_and_fails_loudly(fake_llm):
    client, pid = _project("wai-unknown@example.com")
    doc = _document(client, pid)
    _source_with_excerpt(client, pid)
    _reply(fake_llm, {"items": [{"source_id": str(uuid.uuid4()), "excerpt_id": str(uuid.uuid4()), "stance": "supports", "why": "Trust me."}]})
    with pytest.raises(PermanentTaskError):
        _run_task({"project_id": pid, "action": "find_evidence", "text": "Output rose.", "document_id": doc, "actor": "user:test"})
    assert _suggestions(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "unknown_source"


def test_evidence_suggestions_keep_the_source_and_stance_and_the_model_sees_only_sent_excerpts(fake_llm):
    client, pid = _project("wai-evidence@example.com")
    doc = _document(client, pid)
    sid = _source_with_excerpt(client, pid)
    excerpt_id = _excerpt_id(sid)
    _reply(fake_llm, {"items": [{"source_id": sid, "excerpt_id": excerpt_id, "stance": "supports", "why": "Same direction of effect."}]})
    _run_task({"project_id": pid, "action": "find_evidence", "text": "Output rose.", "document_id": doc, "actor": "user:test"})
    [row] = _suggestions(pid)
    assert row.evidence_json == [{"source_id": sid, "excerpt_id": excerpt_id, "stance": "supports"}]
    [sent] = fake_llm.requests
    assert "Output rose by 12 percent." in json.dumps(json.loads(sent.content))


def test_a_reply_that_repeats_the_prompt_is_refused(fake_llm):
    client, pid = _project("wai-leak@example.com")
    doc = _document(client, pid)
    from app.prompt_registry import load_prompt

    system = load_prompt("workspace_challenge", 1).system
    _reply(fake_llm, {"items": [{"text": system[:200]}]})
    with pytest.raises(PermanentTaskError):
        _run_task({"project_id": pid, "action": "challenge", "text": "A claim.", "document_id": doc, "actor": "user:test"})
    assert _suggestions(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "leaked_prompt"


def test_a_stored_workspace_suggestion_is_decided_through_the_shared_accept_endpoint(fake_llm):
    client, pid = _project("wai-accept@example.com")
    doc = _document(client, pid)
    _reply(fake_llm, {"items": [{"text": "Which countries?"}]})
    _run_task({"project_id": pid, "action": "challenge", "text": "A claim.", "document_id": doc, "actor": "user:test"})
    [row] = _suggestions(pid)
    accepted = client.post(f"/api/projects/{pid}/suggestions/{row.id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "accepted"


def test_the_list_says_which_workspace_action_made_each_suggestion(fake_llm):
    client, pid = _project("wai-list@example.com")
    doc = _document(client, pid)
    _reply(fake_llm, {"items": [{"text": "Which countries?"}]})
    _run_task({"project_id": pid, "action": "challenge", "text": "A claim.", "document_id": doc, "actor": "user:test"})
    [row] = client.get(f"/api/projects/{pid}/suggestions?target_kind=document&target_id={doc}").json()
    assert row["action"] == "challenge"
