"""Figure routes and figure suggestions (plan M8.3): flag gating, human edits, accept/undo provenance, preview errors."""

import json
import uuid

import pytest

from app import figure_spec, figure_store, suggestion_store, suggestions
from app.config import get_settings
from app.database import SessionLocal
from app.models import Figure, UploadedDocument
from tests.api_helpers import create_project, sign_in_user
from tests.test_fulltext import make_pdf

PDF = make_pdf([["Figure source document body text."]])
SPEC = {"nodes": [{"id": "a", "label": "Motivation", "role": "independent", "evidence": []}], "edges": []}
REVISED = {"nodes": [{"id": "a", "label": "Motive", "role": "independent", "evidence": []}], "edges": []}
FLOWCHART_ONLY_ROLE = {"nodes": [{"id": "a", "label": "Start", "role": "start", "evidence": []}], "edges": []}


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def _figure(project_id, owner_id):
    """A figure with version 1, created directly: there is no figure-create route until M8.4."""
    with SessionLocal() as db:
        document = UploadedDocument(
            id=uuid.uuid4(), project_id=uuid.UUID(project_id), filename="paper.pdf", media_type="application/pdf",
            sha256="0" * 64, size=len(PDF), storage_key=f"projects/{project_id}/uploaded-documents/x/original.pdf",
            uploaded_by=owner_id,
        )
        db.add(document)
        db.flush()
        figure = Figure(
            id=uuid.uuid4(), project_id=uuid.UUID(project_id), uploaded_document_id=document.id,
            kind=figure_spec.CONCEPTUAL_FRAMEWORK, title="Framework", created_by=owner_id,
        )
        db.add(figure)
        db.flush()
        figure_store.write_version(db, figure, SPEC, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by=owner_id)
        db.commit()
        return str(figure.id)


def _version_numbers(figure_id):
    with SessionLocal() as db:
        figure = db.get(Figure, uuid.UUID(figure_id))
        return [(version.number, version.origin) for version in figure_store.list_versions(db, figure)]


def test_the_figure_routes_answer_404_while_research_figures_is_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_figures_enabled", False)
    client, _ = sign_in_user("figures-off@example.com")
    project_id = create_project(client, "Figures off")

    response = client.get(f"/api/projects/{project_id}/figures")

    assert response.status_code == 404


def test_a_human_edit_appends_version_two_marked_human_edited():
    client, owner_id = sign_in_user("figures-edit@example.com")
    project_id = create_project(client, "Figures edit")
    figure_id = _figure(project_id, owner_id)

    response = client.post(f"/api/projects/{project_id}/figures/{figure_id}/versions", json={"spec": REVISED})

    assert response.status_code == 201, response.text
    assert _version_numbers(figure_id) == [(1, "human_edited"), (2, "human_edited")]


def test_a_spec_the_figure_kind_cannot_hold_is_refused_and_writes_no_version():
    client, owner_id = sign_in_user("figures-invalid@example.com")
    project_id = create_project(client, "Figures invalid")
    figure_id = _figure(project_id, owner_id)

    response = client.post(
        f"/api/projects/{project_id}/figures/{figure_id}/versions", json={"spec": FLOWCHART_ONLY_ROLE},
    )

    assert response.status_code == 422, response.text
    assert _version_numbers(figure_id) == [(1, "human_edited")]


def test_accepting_a_figure_suggestion_appends_version_two_marked_ai_accepted():
    client, owner_id = sign_in_user("figures-accept@example.com")
    project_id = create_project(client, "Figures accept")
    figure_id = _figure(project_id, owner_id)
    with SessionLocal() as db:
        figure = db.get(Figure, uuid.UUID(figure_id))
        suggestion = suggestion_store.create_suggestion(
            db, project_id=figure.project_id, target_kind="figure", target_id=figure_id,
            content=json.dumps(REVISED), model_id="fake-model", prompt_version="figure_conceptual_framework@1",
            producing_agent="test",
        )
        suggestion_store.act(db, suggestion, suggestions.ACCEPTED, "user:test")
        db.commit()

    assert _version_numbers(figure_id) == [(1, "human_edited"), (2, "ai_accepted")]


def test_undoing_an_accepted_figure_suggestion_appends_a_copy_of_the_earlier_version():
    client, owner_id = sign_in_user("figures-undo@example.com")
    project_id = create_project(client, "Figures undo")
    figure_id = _figure(project_id, owner_id)
    with SessionLocal() as db:
        figure = db.get(Figure, uuid.UUID(figure_id))
        suggestion = suggestion_store.create_suggestion(
            db, project_id=figure.project_id, target_kind="figure", target_id=figure_id,
            content=json.dumps(REVISED), model_id="fake-model", prompt_version="figure_conceptual_framework@1",
            producing_agent="test",
        )
        suggestion_store.act(db, suggestion, suggestions.ACCEPTED, "user:test")
        suggestion_store.act(db, suggestion, suggestions.UNDONE, "user:test")
        db.commit()
        restored = figure_store.latest_version(db, figure)

    assert _version_numbers(figure_id) == [(1, "human_edited"), (2, "ai_accepted"), (3, "human_edited")]
    assert restored.spec_json == figure_spec.normalize(SPEC, figure_spec.CONCEPTUAL_FRAMEWORK)


def test_a_preview_answers_503_when_the_renderer_cannot_start(monkeypatch):
    monkeypatch.setattr(get_settings(), "graphviz_dot_path", "no-such-dot-binary")
    client, owner_id = sign_in_user("figures-preview@example.com")
    project_id = create_project(client, "Figures preview")
    figure_id = _figure(project_id, owner_id)

    response = client.get(f"/api/projects/{project_id}/figures/{figure_id}/versions/1/preview.svg")

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "figure_renderer_unavailable"


@pytest.mark.requires_graphviz
def test_a_preview_returns_the_figure_as_svg_when_graphviz_is_installed():
    client, owner_id = sign_in_user("figures-svg@example.com")
    project_id = create_project(client, "Figures svg")
    figure_id = _figure(project_id, owner_id)

    response = client.get(f"/api/projects/{project_id}/figures/{figure_id}/versions/1/preview.svg")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in response.content
    assert b"Motivation" in response.content
