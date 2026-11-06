import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.database import SessionLocal
from app.models import AuthorshipEvent, AuthorshipKind, Document, DocumentVersion, ProvenanceReport, Project, SpanOrigin, User


def _ledger_rows():
    """One project, one document, one version and one authorship event. Returns their ids."""
    with SessionLocal() as db:
        user = User(email="ledger@example.com", display_name="L")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="P")
        db.add(project)
        db.flush()
        document = Document(project_id=project.id, title="Introduction", position=1, created_by="user:l")
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_id=document.id, project_id=project.id, number=1, content_json={"type": "doc", "content": []},
            content_text="Original text.", content_hash="a" * 64, saved_by="user:l", client_save_id="save-1",
        )
        db.add(version)
        db.flush()
        event = AuthorshipEvent(
            project_id=project.id, document_id=document.id, version_id=version.id, span_id=str(uuid.uuid4()),
            kind=AuthorshipKind.inserted, origin=SpanOrigin.typed, actor="user:l", chars_before=0, chars_after=14,
            text_hash="b" * 64,
        )
        db.add(event)
        db.commit()
        return document.id, version.id, event.id


def test_document_version_orm_update_is_rejected():
    _, version_id, _ = _ledger_rows()
    with SessionLocal() as db:
        version = db.get(DocumentVersion, version_id)
        version.content_text = "Tampered."
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(DocumentVersion, version_id).content_text == "Original text."


def test_document_version_orm_delete_is_rejected():
    _, version_id, _ = _ledger_rows()
    with SessionLocal() as db:
        db.delete(db.get(DocumentVersion, version_id))
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(DocumentVersion, version_id) is not None


def test_authorship_event_orm_update_is_rejected():
    _, _, event_id = _ledger_rows()
    with SessionLocal() as db:
        event = db.get(AuthorshipEvent, event_id)
        event.kind = AuthorshipKind.removed
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(AuthorshipEvent, event_id).kind == AuthorshipKind.inserted


def test_authorship_event_orm_delete_is_rejected():
    _, _, event_id = _ledger_rows()
    with SessionLocal() as db:
        db.delete(db.get(AuthorshipEvent, event_id))
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(AuthorshipEvent, event_id) is not None


def test_raw_sql_update_of_a_version_is_rejected_by_database_trigger():
    _, version_id, _ = _ledger_rows()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("UPDATE document_versions SET content_text = 'Tampered.' WHERE id = :id"), {"id": version_id.hex})
            db.commit()
        db.rollback()
        assert db.get(DocumentVersion, version_id).content_text == "Original text."


def test_raw_sql_delete_of_a_version_is_rejected_by_database_trigger():
    _, version_id, _ = _ledger_rows()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("DELETE FROM document_versions WHERE id = :id"), {"id": version_id.hex})
            db.commit()
        db.rollback()
        assert db.get(DocumentVersion, version_id) is not None


def test_raw_sql_update_of_an_event_is_rejected_by_database_trigger():
    _, _, event_id = _ledger_rows()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("UPDATE authorship_events SET actor = 'user:x' WHERE id = :id"), {"id": event_id.hex})
            db.commit()
        db.rollback()
        assert db.get(AuthorshipEvent, event_id).actor == "user:l"


def test_raw_sql_delete_of_an_event_is_rejected_by_database_trigger():
    _, _, event_id = _ledger_rows()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("DELETE FROM authorship_events WHERE id = :id"), {"id": event_id.hex})
            db.commit()
        db.rollback()
        assert db.get(AuthorshipEvent, event_id) is not None


def test_a_document_is_not_append_only_so_its_current_version_can_move():
    document_id, version_id, _ = _ledger_rows()
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        document.current_version_id = version_id
        db.commit()
    with SessionLocal() as db:
        assert db.get(Document, document_id).current_version_id == version_id


def test_a_second_version_with_the_same_number_is_rejected():
    document_id, _, _ = _ledger_rows()
    with SessionLocal() as db:
        db.add(
            DocumentVersion(
                document_id=document_id, project_id=db.get(Document, document_id).project_id, number=1,
                content_json={}, content_text="", content_hash="c" * 64, saved_by="user:l", client_save_id="save-2",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_retried_save_with_the_same_client_save_id_is_rejected_by_the_database():
    document_id, _, _ = _ledger_rows()
    with SessionLocal() as db:
        db.add(
            DocumentVersion(
                document_id=document_id, project_id=db.get(Document, document_id).project_id, number=2,
                content_json={}, content_text="", content_hash="c" * 64, saved_by="user:l", client_save_id="save-1",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_version_rows_are_readable_after_writing():
    _, version_id, event_id = _ledger_rows()
    with SessionLocal() as db:
        assert db.scalar(select(DocumentVersion.number).where(DocumentVersion.id == version_id)) == 1
        assert db.scalar(select(AuthorshipEvent.origin).where(AuthorshipEvent.id == event_id)) == SpanOrigin.typed


def test_raw_sql_update_of_a_provenance_report_is_rejected_by_database_trigger():
    document_id, _, _ = _ledger_rows()
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        report = ProvenanceReport(
            project_id=document.project_id, scope="section", document_id=document_id, covered_versions=[], report_json={},
            statement_text="Original.", statement_template="ai_use_statement@1", report_hash="c" * 64, generated_by="user:l",
        )
        db.add(report)
        db.commit()
        report_id = report.id
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text("UPDATE provenance_reports SET statement_text = 'Tampered.' WHERE id = :id"), {"id": report_id.hex})
            db.commit()
        db.rollback()
        assert db.get(ProvenanceReport, report_id).statement_text == "Original."
