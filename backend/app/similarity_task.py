"""The `similarity_check` task body (X.31.14).

The router queues this task with the section and the saved version it should check, and returns at once. The task
reads that version and this project's source excerpts and abstracts, runs the pure rules in `app/similarity.py`, and
stores one append-only `SimilarityCheck` row. No model and no network are involved.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, ledger, similarity
from app.database import SessionLocal
from app.models import Document, DocumentVersion, Project, SimilarityCheck, Source, SourceExcerpt
from app.task_registry import PermanentTaskError


def latest_version(db: Session, document_id: uuid.UUID) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.number.desc()).limit(1)
    )


def references(db: Session, project_id: uuid.UUID) -> list[similarity.Reference]:
    """Excerpts and abstracts of this project's sources, not counting sources merged into another."""
    found: list[similarity.Reference] = []
    excerpts = db.execute(
        select(SourceExcerpt.source_id, SourceExcerpt.id, SourceExcerpt.content)
        .join(Source, Source.id == SourceExcerpt.source_id)
        .where(Source.project_id == project_id, Source.merged_into.is_(None))
    )
    for source_id, excerpt_id, content in excerpts:
        found.append(similarity.Reference(source_id=str(source_id), excerpt_id=str(excerpt_id), text=content))
    abstracts = db.execute(
        select(Source.id, Source.abstract).where(Source.project_id == project_id, Source.merged_into.is_(None), Source.abstract.is_not(None))
    )
    for source_id, abstract in abstracts:
        found.append(similarity.Reference(source_id=str(source_id), excerpt_id=None, text=abstract))
    return found


def run_similarity_check(payload: dict) -> None:
    """The body of the `similarity_check` task. Raises `PermanentTaskError` when the request no longer makes sense."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        document_id = uuid.UUID(str(payload["document_id"]))
        version_id = uuid.UUID(str(payload["version_id"]))
        provider = str(payload["provider"])
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A similarity_check task needs project_id, document_id, version_id and provider.") from exc

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        document = db.get(Document, document_id)
        if project is None or document is None or document.project_id != project.id:
            raise PermanentTaskError("The section for this similarity check is no longer in the project.")
        version = db.get(DocumentVersion, version_id)
        if version is None or version.document_id != document.id:
            raise PermanentTaskError("The saved version for this similarity check no longer exists.")

        paragraphs = [
            similarity.Paragraph(text=text, quoted=quoted, cited=cited)
            for text, quoted, cited in ledger.text_blocks(version.content_json)
        ]
        findings = similarity.check(paragraphs, references(db, project.id))
        summary = similarity.summarise(findings)
        row = SimilarityCheck(
            project_id=project.id, document_id=document.id, version_id=version.id, provider=provider,
            status="completed", requested_by=actor, findings_json=findings, summary_json=summary,
            # Set here, not by the database: SQLite's now() has one-second resolution, so two checks in a second tie.
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.flush()
        audit.record(db, actor=actor, action="similarity.completed", project_id=project.id,
                     payload={"document_id": str(document.id), "version": version.number, "check_id": str(row.id), **summary})
        db.commit()
    finally:
        db.close()
