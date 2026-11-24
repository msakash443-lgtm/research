"""Figure generation, queued half (plan M8.4): the request route, the handler's stored suggestion, and refusals."""

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import AiSuggestion, AuditEvent, Figure, Task
from app.prompt_registry import load_prompt
from app.task_handlers import FIGURE_GENERATE
from app.task_registry import PermanentTaskError, get_handler
from tests.api_helpers import create_project, sign_in
from tests.test_fulltext import make_pdf

PDF = make_pdf([["Trust predicts collaboration among partners. Methods use a survey."]])
GOOD_REPLY = {
    "nodes": [
        {"id": "trust", "label": "Trust", "role": "independent", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"id": "collab", "label": "Collaboration", "role": "dependent", "evidence": [{"quote": "collaboration among partners", "section": None}]},
    ],
    "edges": [
        {"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
    ],
}
INVENTED_REPLY = {
    "nodes": [
        {"id": "trust", "label": "Trust", "role": "independent", "evidence": [{"quote": "Sample was 900 firms", "section": None}]},
    ],
    "edges": [],
}


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def _figure_project(email, *, ai="suggestions_only"):
    client = sign_in(email)
    pid = create_project(client, "Figure Generate")
    if ai != "suggestions_only":
        assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": ai}).status_code == 200
    return client, pid


def _upload(client, pid):
    response = client.post(
        f"/api/projects/{pid}/uploaded-documents/upload?filename=paper.pdf",
        content=PDF, headers={"content-type": "application/pdf"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _request(client, pid, upload_id, kind="conceptual_framework"):
    return client.post(f"/api/projects/{pid}/figures", json={"uploaded_document_id": upload_id, "kind": kind})


def _reply(fake_llm, payload):
    fake_llm.handler = lambda request: {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _run_figure_generate(payload):
    handler = get_handler(FIGURE_GENERATE)
    handler(SimpleNamespace(payload=payload, ensure_owned=lambda _db=None: None))


def _suggestions(pid):
    with SessionLocal() as db:
        return db.scalars(select(AiSuggestion).where(AiSuggestion.project_id == uuid.UUID(pid)).order_by(AiSuggestion.created_at)).all()


def _audit(pid, action):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == action)).all()
        return [dict(r.payload_json or {}) for r in rows]


def _figures(pid):
    with SessionLocal() as db:
        return db.scalars(select(Figure).where(Figure.project_id == uuid.UUID(pid))).all()


def test_the_figure_request_route_answers_404_while_research_figures_is_off(monkeypatch, fake_llm):
    client, pid = _figure_project("fg-flag-off@example.com")
    upload_id = _upload(client, pid)
    monkeypatch.setattr(get_settings(), "research_figures_enabled", False)
    response = _request(client, pid, upload_id)
    assert response.status_code == 404
    assert fake_llm.requests == []


def test_with_ai_off_a_figure_request_is_refused_and_no_figure_is_created(fake_llm):
    client, pid = _figure_project("fg-ai-off@example.com", ai="off")
    upload_id = _upload(client, pid)
    response = _request(client, pid, upload_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_off"
    assert _figures(pid) == []
    assert fake_llm.requests == []


def test_a_valid_figure_request_queues_one_task_and_audits_it(fake_llm):
    client, pid = _figure_project("fg-queue@example.com")
    upload_id = _upload(client, pid)
    response = _request(client, pid, upload_id)
    assert response.status_code == 202
    body = response.json()
    with SessionLocal() as db:
        task = db.get(Task, uuid.UUID(body["task_id"]))
        assert task.type == FIGURE_GENERATE and task.status.value == "queued"
    [requested] = _audit(pid, "ai.requested")
    assert requested["target_kind"] == "figure" and requested["target_id"] == body["figure_id"]
    assert fake_llm.requests == []  # queued, not yet sent


def test_the_handler_stores_one_pending_suggestion_with_the_model_and_prompt_recorded(fake_llm):
    client, pid = _figure_project("fg-handler@example.com")
    upload_id = _upload(client, pid)
    figure_id = _request(client, pid, upload_id).json()["figure_id"]
    _reply(fake_llm, GOOD_REPLY)
    _run_figure_generate({"project_id": pid, "figure_id": figure_id, "actor": "user:test"})
    [suggestion] = _suggestions(pid)
    assert suggestion.target_kind == "figure" and suggestion.target_id == figure_id
    assert suggestion.model_id == "fake-model"
    assert suggestion.prompt_version == load_prompt("figure_conceptual_framework", 1).ref
    [proposed] = _audit(pid, "ai.proposed")
    assert proposed["target_kind"] == "figure"
    assert _figures(pid)[0].id == uuid.UUID(figure_id)
    assert len(fake_llm.requests) == 1


def test_an_invented_quote_stores_nothing_and_records_the_failure(fake_llm):
    client, pid = _figure_project("fg-invented@example.com")
    upload_id = _upload(client, pid)
    figure_id = _request(client, pid, upload_id).json()["figure_id"]
    _reply(fake_llm, INVENTED_REPLY)
    with pytest.raises(PermanentTaskError):
        _run_figure_generate({"project_id": pid, "figure_id": figure_id, "actor": "user:test"})
    assert _suggestions(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "unsupported_element"


FLOW_GOOD_REPLY = {
    "nodes": [
        {"id": "s", "label": "Start", "role": "start", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"id": "p", "label": "Survey", "role": "process", "evidence": [{"quote": "Methods use a survey", "section": None}]},
        {"id": "end", "label": "Done", "role": "end", "evidence": [{"quote": "Methods use a survey", "section": None}]},
    ],
    "edges": [
        {"from": "s", "to": "p", "kind": "sequence", "label": "", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"from": "p", "to": "end", "kind": "sequence", "label": "", "evidence": [{"quote": "Methods use a survey", "section": None}]},
    ],
}
FLOW_TWO_STARTS_REPLY = {
    "nodes": [
        {"id": "s1", "label": "Start one", "role": "start", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"id": "s2", "label": "Start two", "role": "start", "evidence": [{"quote": "Methods use a survey", "section": None}]},
        {"id": "end", "label": "Done", "role": "end", "evidence": [{"quote": "Methods use a survey", "section": None}]},
    ],
    "edges": [
        {"from": "s1", "to": "end", "kind": "sequence", "label": "", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"from": "s2", "to": "end", "kind": "sequence", "label": "", "evidence": [{"quote": "Methods use a survey", "section": None}]},
    ],
}


def test_a_methodology_flowchart_request_stores_one_pending_suggestion_under_the_flowchart_prompt(fake_llm):
    client, pid = _figure_project("fg-flow-handler@example.com")
    upload_id = _upload(client, pid)
    figure_id = _request(client, pid, upload_id, kind="methodology_flowchart").json()["figure_id"]
    _reply(fake_llm, FLOW_GOOD_REPLY)
    _run_figure_generate({"project_id": pid, "figure_id": figure_id, "actor": "user:test"})
    [suggestion] = _suggestions(pid)
    assert suggestion.target_kind == "figure" and suggestion.target_id == figure_id
    assert suggestion.prompt_version == load_prompt("figure_methodology_flowchart", 1).ref


def test_a_two_start_flowchart_stores_nothing_and_records_invalid_structure(fake_llm):
    client, pid = _figure_project("fg-flow-two-starts@example.com")
    upload_id = _upload(client, pid)
    figure_id = _request(client, pid, upload_id, kind="methodology_flowchart").json()["figure_id"]
    _reply(fake_llm, FLOW_TWO_STARTS_REPLY)
    with pytest.raises(PermanentTaskError):
        _run_figure_generate({"project_id": pid, "figure_id": figure_id, "actor": "user:test"})
    assert _suggestions(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "invalid_structure"
