"""Figure accuracy check (plan M8.6): the task stores a labelled check or an abstention, refuses an incomplete reply
and a withdrawn document, and the check routes queue the task and answer the findings with the latest AI check."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import figure_store
from app.config import get_settings
from app.database import SessionLocal
from app.figure_accuracy import PROMPT
from app.models import AuditEvent, Figure, FigureCheck, UploadedDocument
from app.prompt_registry import load_prompt
from app.task_handlers import FIGURE_ACCURACY_CHECK
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
UNSUPPORTED_QUOTE_SPEC = {
    "nodes": [{"id": "trust", "label": "Trust", "role": "independent", "evidence": [{"quote": "Sample was 900 firms", "section": None}]}],
    "edges": [],
}


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def _check_project(email, *, ai="suggestions_only"):
    client = sign_in(email)
    pid = create_project(client, "Figure Accuracy")
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


def _figure(pid, upload_id, spec=SPEC):
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


def _labelled(labels, confidence=0.8):
    return {
        "labels": [{"element_id": element_id, "label": label} for element_id, label in labels.items()],
        "confidence": confidence,
        "insufficient_evidence": {"insufficient": False, "reason": ""},
    }


def _abstention(reason):
    return {"labels": [], "confidence": 0, "insufficient_evidence": {"insufficient": True, "reason": reason}}


def _reply(fake_llm, payload):
    fake_llm.handler = lambda request: {"choices": [{"message": {"content": json.dumps(payload)}}]}


def _run_check(pid, figure_id, number=1):
    handler = get_handler(FIGURE_ACCURACY_CHECK)
    payload = {"project_id": pid, "figure_id": figure_id, "number": number, "actor": "test"}
    handler(SimpleNamespace(payload=payload, ensure_owned=lambda _db=None: None))


def _checks(pid):
    with SessionLocal() as db:
        return db.scalars(select(FigureCheck).where(FigureCheck.project_id == uuid.UUID(pid))).all()


def _version_count(figure_id):
    with SessionLocal() as db:
        return len(figure_store.list_versions(db, db.get(Figure, uuid.UUID(figure_id))))


def _audit(pid, action):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == action)).all()
        return [dict(r.payload_json or {}) for r in rows]


def test_a_labelled_check_is_stored_with_its_labels_and_pinned_prompt_and_leaves_the_figure_unchanged(fake_llm):
    client, pid = _check_project("ac-labelled@example.com")
    figure_id = _figure(pid, _upload(client, pid))
    _reply(fake_llm, _labelled({"trust": "supported", "collab": "unsupported", "edge:0": "supported"}))

    _run_check(pid, figure_id)

    [check] = _checks(pid)
    assert check.status == "checked"
    assert check.labels_json == {"trust": "supported", "collab": "unsupported", "edge:0": "supported"}
    assert check.prompt_version == load_prompt(*PROMPT).ref
    assert _version_count(figure_id) == 1


def test_an_abstention_is_stored_as_insufficient_evidence_with_its_reason_and_no_labels(fake_llm):
    client, pid = _check_project("ac-abstain@example.com")
    figure_id = _figure(pid, _upload(client, pid))
    _reply(fake_llm, _abstention("The paper does not describe the partners."))

    _run_check(pid, figure_id)

    [check] = _checks(pid)
    assert check.status == "insufficient_evidence"
    assert check.labels_json == {}
    assert check.reason == "The paper does not describe the partners."


def test_a_reply_that_leaves_out_an_element_is_refused_and_stores_no_check(fake_llm):
    client, pid = _check_project("ac-incomplete@example.com")
    figure_id = _figure(pid, _upload(client, pid))
    _reply(fake_llm, _labelled({"trust": "supported", "collab": "supported"}))

    with pytest.raises(PermanentTaskError):
        _run_check(pid, figure_id)

    assert _checks(pid) == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "incomplete_labels"


def test_a_check_on_a_withdrawn_document_is_refused_before_any_model_call(fake_llm):
    client, pid = _check_project("ac-withdrawn@example.com")
    upload_id = _upload(client, pid)
    figure_id = _figure(pid, upload_id)
    with SessionLocal() as db:
        db.get(UploadedDocument, uuid.UUID(upload_id)).withdrawn_at = datetime.now(UTC)
        db.commit()

    with pytest.raises(PermanentTaskError):
        _run_check(pid, figure_id)

    assert fake_llm.requests == []
    [failed] = _audit(pid, "ai.failed")
    assert failed["code"] == "document_withdrawn"


def test_requesting_a_check_answers_202_and_records_the_request(fake_llm):
    client, pid = _check_project("ac-request@example.com")
    figure_id = _figure(pid, _upload(client, pid))

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions/1/check")

    assert response.status_code == 202
    assert response.json()["figure_id"] == figure_id
    assert [event["action"] for event in _audit(pid, "ai.requested")] == ["check"]


def test_requesting_a_check_with_ai_off_answers_409_and_queues_nothing(fake_llm):
    client, pid = _check_project("ac-request-off@example.com", ai="off")
    figure_id = _figure(pid, _upload(client, pid))

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions/1/check")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_off"
    assert _audit(pid, "ai.requested") == []


def test_requesting_a_check_for_a_version_that_does_not_exist_answers_404(fake_llm):
    client, pid = _check_project("ac-request-missing@example.com")
    figure_id = _figure(pid, _upload(client, pid))

    response = client.post(f"/api/projects/{pid}/figures/{figure_id}/versions/9/check")

    assert response.status_code == 404


def test_the_check_read_shows_the_deterministic_findings_and_then_the_latest_ai_check(fake_llm):
    client, pid = _check_project("ac-read@example.com")
    figure_id = _figure(pid, _upload(client, pid), spec=UNSUPPORTED_QUOTE_SPEC)

    before = client.get(f"/api/projects/{pid}/figures/{figure_id}/versions/1/check")
    assert before.status_code == 200
    assert before.json()["ai"] is None
    assert "quote_not_found" in [finding["code"] for finding in before.json()["deterministic"]]

    _reply(fake_llm, _labelled({"trust": "unsupported"}))
    _run_check(pid, figure_id)  # the spec has no edges, so its only element is `trust`
    after = client.get(f"/api/projects/{pid}/figures/{figure_id}/versions/1/check")

    assert after.status_code == 200
    assert after.json()["ai"]["status"] == "checked"
    assert after.json()["ai"]["labels"] == {"trust": "unsupported"}
    assert after.json()["ai"]["prompt_version"] == load_prompt(*PROMPT).ref
