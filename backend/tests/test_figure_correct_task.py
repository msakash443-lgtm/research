"""Figure correction (plan M8.7): the task stores a corrected spec as a pending suggestion only when it passes the
deterministic checks, and refuses a reply that still fails them or quotes text the paper does not contain."""

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import figure_correction, figure_store
from app.config import get_settings
from app.database import SessionLocal
from app.models import AiSuggestion, AuditEvent, Figure
from app.prompt_registry import load_prompt
from app.task_handlers import FIGURE_CORRECT
from app.task_registry import PermanentTaskError, get_handler
from tests.api_helpers import create_project, sign_in
from tests.test_fulltext import make_pdf

PDF = make_pdf([["Trust predicts collaboration among partners. Methods use a survey."]])
SPEC = {
    "nodes": [
        {"id": "trust", "label": "Trust", "role": "independent", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"id": "collab", "label": "Collaboration", "role": "dependent", "evidence": []},
    ],
    "edges": [{"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": []}],
}
# The same trust node, with an edge to a node the figure does not have: a dangling_edge ERROR finding.
DANGLING = {
    "nodes": [SPEC["nodes"][0]],
    "edges": [{"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": []}],
}
UNSUPPORTED_QUOTE_SPEC = {
    "nodes": [{"id": "trust", "label": "Trust", "role": "independent", "evidence": [{"quote": "Sample was 900 firms", "section": None}]}],
    "edges": [],
}


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def _correct_project(email, *, ai="suggestions_only"):
    client = sign_in(email)
    pid = create_project(client, "Figure Correction")
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


def _figure(pid, upload_id, spec):
    """A figure with version 1 written directly: the figure-create route queues a model call, which these tests avoid."""
    with SessionLocal() as db:
        figure = Figure(
            id=uuid.uuid4(), project_id=uuid.UUID(pid), uploaded_document_id=uuid.UUID(upload_id),
            kind="conceptual_framework", title="Framework", created_by="test",
        )
        db.add(figure)
        db.flush()
        figure_store.write_version(db, figure, spec, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by="test")
        db.commit()
        return str(figure.id)


def _reply(fake_llm, payload):
    fake_llm.handler = lambda request: {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _run_correction(pid, figure_id, number=1):
    handler = get_handler(FIGURE_CORRECT)
    payload = {"project_id": pid, "figure_id": figure_id, "number": number, "actor": "test"}
    handler(SimpleNamespace(payload=payload, ensure_owned=lambda _db=None: None))


def _corrections(pid):
    with SessionLocal() as db:
        return db.scalars(
            select(AiSuggestion).where(AiSuggestion.project_id == uuid.UUID(pid), AiSuggestion.producing_agent == "figure_correct")
        ).all()


def _version_count(figure_id):
    with SessionLocal() as db:
        return len(figure_store.list_versions(db, db.get(Figure, uuid.UUID(figure_id))))


def _audit(pid, action):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == action)).all()
        return [dict(r.payload_json or {}) for r in rows]


def test_a_corrected_spec_that_passes_the_checks_is_stored_as_a_pending_suggestion_and_leaves_the_version_unchanged(fake_llm):
    client, pid = _correct_project("fc-stored@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)
    _reply(fake_llm, SPEC)

    _run_correction(pid, figure_id)

    [suggestion] = _corrections(pid)
    assert {node["id"] for node in json.loads(suggestion.content)["nodes"]} == {"trust", "collab"}
    assert suggestion.prompt_version == load_prompt(*figure_correction.PROMPT).ref
    with SessionLocal() as db:
        base_hash = figure_store.version_by_number(db, db.get(Figure, uuid.UUID(figure_id)), 1).spec_hash
    assert suggestion.input_hash == base_hash
    assert _version_count(figure_id) == 1


def test_a_correction_that_still_fails_the_checks_is_refused_and_stores_nothing(fake_llm):
    client, pid = _correct_project("fc-still-invalid@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)
    _reply(fake_llm, DANGLING)

    with pytest.raises(PermanentTaskError):
        _run_correction(pid, figure_id)

    assert _corrections(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == figure_correction.CODE


def test_a_correction_quoting_text_the_paper_does_not_contain_is_refused_and_stores_nothing(fake_llm):
    client, pid = _correct_project("fc-bad-quote@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)
    _reply(fake_llm, UNSUPPORTED_QUOTE_SPEC)

    with pytest.raises(PermanentTaskError):
        _run_correction(pid, figure_id)

    assert _corrections(pid) == []
    assert len(_audit(pid, "ai.failed")) == 1
