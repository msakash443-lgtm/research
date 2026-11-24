"""Figure version storage (plan M8.3): numbering, provenance and the append-only trigger."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import figure_spec, figure_store, ledger_guard, suggestion_store
from app.database import SessionLocal, engine
from app.models import Figure, Project, UploadedDocument, User

SPEC = {"nodes": [{"id": "a", "label": "Motivation", "role": "independent", "evidence": []}], "edges": []}
REVISED = {"nodes": [{"id": "a", "label": "Motive", "role": "independent", "evidence": []}], "edges": []}


def _figure(db):
    user = User(email=f"figures-{uuid.uuid4().hex}@example.test")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title="Figures")
    db.add(project)
    db.flush()
    document = UploadedDocument(
        id=uuid.uuid4(), project_id=project.id, filename="paper.pdf", media_type="application/pdf",
        sha256="0" * 64, size=1, storage_key=f"projects/{project.id}/uploaded-documents/x/original.pdf",
        uploaded_by="test",
    )
    db.add(document)
    db.flush()
    figure = Figure(
        id=uuid.uuid4(), project_id=project.id, uploaded_document_id=document.id,
        kind=figure_spec.CONCEPTUAL_FRAMEWORK, title="Framework", created_by="test",
    )
    db.add(figure)
    db.flush()
    return figure


def test_each_written_version_is_numbered_one_more_than_the_last():
    with SessionLocal() as db:
        figure = _figure(db)

        first = figure_store.write_version(db, figure, SPEC, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by="test")
        second = figure_store.write_version(db, figure, REVISED, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by="test")

        assert (first.number, second.number) == (1, 2)
        assert figure_store.latest_version(db, figure).number == 2
        assert figure_store.version_by_number(db, figure, 1).spec_json == figure_spec.normalize(SPEC, figure.kind)


def test_a_version_written_from_a_suggestion_records_its_model_and_prompt_version():
    with SessionLocal() as db:
        figure = _figure(db)
        suggestion = suggestion_store.create_suggestion(
            db, project_id=figure.project_id, target_kind="figure", target_id=str(figure.id),
            content='{"nodes": [], "edges": []}', model_id="fake-model", prompt_version="figure_conceptual_framework@1",
            producing_agent="test",
        )

        version = figure_store.write_version(
            db, figure, SPEC, origin=figure_store.ORIGIN_AI_ACCEPTED, created_by="test", suggestion=suggestion,
        )

        assert version.origin == figure_store.ORIGIN_AI_ACCEPTED
        assert version.suggestion_id == suggestion.id
        assert version.model_id == "fake-model"
        assert version.prompt_version == "figure_conceptual_framework@1"


@pytest.mark.real_db
def test_figure_versions_refuse_update_and_delete_on_the_database():
    with SessionLocal() as db:
        figure = _figure(db)
        version = figure_store.write_version(db, figure, SPEC, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by="test")
        db.commit()
        version_id = version.id

    for sql in (
        "UPDATE figure_versions SET origin = 'tampered' WHERE id = :id",
        "DELETE FROM figure_versions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match=ledger_guard.FIGURE_VERSIONS_MESSAGE):
            with engine.begin() as conn:
                conn.execute(text(sql), {"id": version_id})
