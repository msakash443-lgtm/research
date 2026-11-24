"""Figure correction routes (plan M8.7): requesting a correction queues it or answers 409 with AI off, the suggestion read
diffs the proposal from the version it corrects, accepting it writes an AI-accepted version, and a person's edit
records how many elements each kind of change touched."""

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import figure_store
from app.config import get_settings
from app.database import SessionLocal
from app.models import AiSuggestion, AuditEvent, Figure
from app.task_handlers import FIGURE_CORRECT
from app.task_registry import get_handler
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
DANGLING = {
    "nodes": [SPEC["nodes"][0]],
    "edges": [{"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": []}],
}


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def _correct_project(email, *, ai="suggestions_only"):
    client = sign_in(email)
    pid = create_project(client, "Figure Correction Routes")
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


def _correction_id(pid):
    with SessionLocal() as db:
        [row] = db.scalars(
            select(AiSuggestion).where(AiSuggestion.project_id == uuid.UUID(pid), AiSuggestion.producing_agent == "figure_correct")
        ).all()
        return str(row.id)


def _audit(pid, action):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == action)).all()
        return [dict(r.payload_json or {}) for r in rows]


def test_requesting_a_correction_answers_202_and_records_the_request_for_the_correct_action(fake_llm):
    client, pid = _correct_project("fcr-request@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions/1/correct")

    assert response.status_code == 202
    assert [event["action"] for event in _audit(pid, "ai.requested")] == ["correct"]


def test_requesting_a_correction_with_ai_off_answers_409_and_queues_nothing(fake_llm):
    client, pid = _correct_project("fcr-ai-off@example.com", ai="off")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions/1/correct")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_off"
    assert _audit(pid, "ai.requested") == []


def test_requesting_a_correction_for_a_version_that_does_not_exist_answers_404(fake_llm):
    client, pid = _correct_project("fcr-missing@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions/9/correct")

    assert response.status_code == 404


def test_the_suggestion_read_diffs_the_proposed_spec_from_the_version_it_corrects(fake_llm):
    client, pid = _correct_project("fcr-diff@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)
    _reply(fake_llm, SPEC)
    _run_correction(pid, figure_id)

    body = client.get(f"/api/projects/{pid}/figures/{figure_id}/suggestions/{_correction_id(pid)}").json()

    assert body["diff"] == {
        "added_nodes": ["collab"], "removed_nodes": [], "changed_nodes": [], "added_edges": [], "removed_edges": [],
    }


def test_accepting_a_correction_writes_an_ai_accepted_version_with_the_corrected_spec(fake_llm):
    client, pid = _correct_project("fcr-accept@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)
    _reply(fake_llm, SPEC)
    _run_correction(pid, figure_id)

    accepted = client.post(f"/api/projects/{pid}/suggestions/{_correction_id(pid)}/accept")

    assert accepted.status_code == 200
    with SessionLocal() as db:
        version = figure_store.version_by_number(db, db.get(Figure, uuid.UUID(figure_id)), 2)
        assert version.origin == figure_store.ORIGIN_AI_ACCEPTED
        assert {node["id"] for node in version.spec_json["nodes"]} == {"trust", "collab"}


def test_a_person_edit_records_how_many_elements_of_each_kind_it_changed(fake_llm):
    client, pid = _correct_project("fcr-edit-audit@example.com")
    figure_id = _figure(pid, _upload(client, pid), DANGLING)

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions", json={"spec": SPEC})

    assert response.status_code == 201
    [event] = _audit(pid, "figure.version_created")
    assert event["number"] == 2
    assert event["origin"] == figure_store.ORIGIN_HUMAN_EDITED
    assert event["changes"] == {"added_nodes": 1, "removed_nodes": 0, "changed_nodes": 0, "added_edges": 0, "removed_edges": 0}
