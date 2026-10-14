"""Similarity check of a manuscript section against this project's sources (X.31.14).

POST queues a `similarity_check` task for the section's latest saved version and returns 202; the task (`similarity_task.py`)
runs the pure rules in `app/similarity.py` and stores one append-only `SimilarityCheck` row. Nothing is sent outside the server.
Only the `project_sources` provider exists; an institutional provider reports `not_configured` until its licence
is confirmed (Q17, X.31.15).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, manuscript_export, similarity_providers
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Document, DocumentVersion, Project, SimilarityCheck, Source, User
from app.refmanager import to_bibtex
from app.similarity_task import latest_version
from app.task_handlers import SIMILARITY_CHECK
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}", tags=["integrity"])

PROJECT_SOURCES = "project_sources"
INSTITUTIONAL_PREFIX = "institutional:"


class SimilarityCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    provider: str = Field(default=PROJECT_SOURCES, min_length=1, max_length=60)


class SimilarityFinding(BaseModel):
    start: int
    end: int
    kind: str
    source_id: str | None
    excerpt_id: str | None
    source_title: str | None
    score: float


class SimilarityCheckRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    provider: str
    status: str
    requested_by: str
    findings: list[SimilarityFinding]
    summary: dict[str, int]
    created_at: datetime


def _document(db: Session, project: Project, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _read(db: Session, row: SimilarityCheck) -> SimilarityCheckRead:
    source_ids = {uuid.UUID(f["source_id"]) for f in row.findings_json if f["source_id"]}
    titles = dict(db.execute(select(Source.id, Source.title).where(Source.id.in_(source_ids))).all()) if source_ids else {}
    findings = [
        SimilarityFinding(
            start=f["start"], end=f["end"], kind=f["kind"], source_id=f["source_id"], excerpt_id=f["excerpt_id"],
            source_title=titles.get(uuid.UUID(f["source_id"])) if f["source_id"] else None, score=f["score"],
        )
        for f in row.findings_json
    ]
    return SimilarityCheckRead(
        id=row.id, project_id=row.project_id, document_id=row.document_id, version_id=row.version_id,
        provider=row.provider, status=row.status, requested_by=row.requested_by,
        findings=findings, summary=row.summary_json, created_at=row.created_at,
    )


@router.post("/similarity-checks", status_code=status.HTTP_202_ACCEPTED)
def create_check(
    payload: SimilarityCheckCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue a check of the section's latest saved version. The result appears in the list once the task has run."""
    if payload.provider.startswith(INSTITUTIONAL_PREFIX):
        # A connector would be queued here once one exists; until then the request is refused and nothing is written.
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "not_configured", "message": similarity_providers.NOT_CONNECTED_HINT})
    if payload.provider != PROJECT_SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown similarity provider {payload.provider!r}")

    document = _document(db, project, payload.document_id)
    latest = latest_version(db, document.id)
    if latest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This section has no saved versions yet")

    actor = audit.user_actor(user)
    # The version is pinned now, so a save made while the task waits does not change what is checked.
    task = enqueue_task(
        db,
        project.id,
        SIMILARITY_CHECK,
        payload={
            "project_id": str(project.id), "document_id": str(document.id), "version_id": str(latest.id),
            "provider": payload.provider, "actor": actor,
        },
        actor=actor,
    )
    audit.record(db, actor=actor, action="similarity.requested", project_id=project.id,
                 payload={"document_id": str(document.id), "version": latest.number, "task_id": str(task.id)})
    db.commit()
    return {"task_id": str(task.id), "status": task.status.value}


@router.get("/similarity-providers")
def list_providers(project: Project = Depends(project_access(READ_ROLES))):
    """Which similarity checks can be run: the project-sources check always, the institutional one only when connected (X.31.15)."""
    return similarity_providers.provider_statuses(get_settings())


@router.get("/similarity-checks", response_model=list[SimilarityCheckRead])
def list_checks(
    document_id: uuid.UUID | None = Query(default=None),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Checks newest first: every check in the project, or only those of one section when `document_id` is given.
    Results are kept, so an old check still shows what it found."""
    statement = select(SimilarityCheck).where(SimilarityCheck.project_id == project.id)
    if document_id is not None:
        statement = statement.where(SimilarityCheck.document_id == _document(db, project, document_id).id)
    rows = db.scalars(statement.order_by(SimilarityCheck.created_at.desc(), SimilarityCheck.id)).all()
    return [_read(db, row) for row in rows]


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXPORT_EXTENSIONS = {"md": "md", "json": "json", "docx": "docx", "bibtex": "bib"}


@router.get("/manuscript/export", response_model=None)
def export_manuscript(
    fmt: Literal["md", "docx", "json", "bibtex"] = Query(default="md", alias="format"),
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The latest saved version of every section, in section order, as Markdown, Word, JSON or BibTeX (X.31.13).

    Sections never saved are left out (their count is in `X-Unsaved-Sections`); with nothing saved the export holds only
    the title, so a read never fails on an empty manuscript. Citations to unverified sources are marked and left out of the references and the BibTeX (plan rule 24).
    Audited with the format and the section count. Nothing here calls a model."""
    documents = db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.position, Document.created_at)).all()
    saved: list[tuple[Document, DocumentVersion]] = []
    for document in documents:
        version = latest_version(db, document.id)
        if version is not None:
            saved.append((document, version))
    sections = [manuscript_export.Section(title=document.title, content=version.content_json) for document, version in saved]
    cited_ids: set[str] = set()
    for section in sections:
        cited_ids.update(manuscript_export.cited_source_ids(section.content))
    source_rows: dict[str, Source] = {}
    if cited_ids:
        wanted = [uuid.UUID(i) for i in cited_ids if _is_uuid(i)]
        source_rows = {str(s.id): s for s in db.scalars(select(Source).where(Source.project_id == project.id, Source.id.in_(wanted)))}
    sources = {
        key: manuscript_export.CitedSource(
            id=key, title=row.title, authors=tuple(row.authors or ()), year=row.year, venue=row.venue, doi=row.doi,
            verified=bool(row.metadata_verified),
        )
        for key, row in source_rows.items()
    }
    references = manuscript_export.references(sections, sources)

    audit.record(
        db, actor=audit.user_actor(user), action="manuscript.exported", project_id=project.id,
        payload={"format": fmt, "sections": len(saved), "references": len(references), "unsaved_sections": len(documents) - len(saved)},
    )
    db.commit()

    headers = {
        "Content-Disposition": f'attachment; filename="manuscript.{EXPORT_EXTENSIONS[fmt]}"',
        "X-Unsaved-Sections": str(len(documents) - len(saved)),
    }
    if fmt == "md":
        return Response(manuscript_export.to_markdown(project.title, sections, sources), media_type="text/markdown; charset=utf-8", headers=headers)
    if fmt == "docx":
        return Response(manuscript_export.to_docx(project.title, sections, sources), media_type=DOCX_MEDIA_TYPE, headers=headers)
    if fmt == "bibtex":
        records = [
            {k: v for k, v in {"title": source_rows[s.id].title, "authors": source_rows[s.id].authors, "year": s.year, "doi": s.doi,
                               "url": source_rows[s.id].url, "venue": s.venue, "source_type": source_rows[s.id].source_type}.items() if v}
            for s in references
        ]
        return Response(to_bibtex(records), media_type="text/plain; charset=utf-8", headers=headers)
    body = manuscript_export.to_json(
        project.title,
        [
            {"document_id": str(d.id), "title": d.title, "position": d.position, "version": v.number,
             "content_json": v.content_json, "content_text": v.content_text}
            for d, v in saved
        ],
        references,
    )
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/json", headers=headers)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
