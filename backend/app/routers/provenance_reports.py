"""Writing provenance report for manuscript sections (X.31.12).

POST freezes the latest saved version of one section, or of every section, and returns the report with its AI-use
statement. GET lists reports (with whether each is stale) and returns one as JSON or Markdown. Nothing here calls a model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import provenance_report as pr
from app import provenance_store
from app.database import get_db
from app.dependencies import READ_ROLES, current_user, project_access
from app.models import ArtifactStatus, Document, Project, ProvenanceReport, User

router = APIRouter(prefix="/projects/{project_id}", tags=["provenance"])


class ProvenanceReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["section", "manuscript"]
    document_id: uuid.UUID | None = None
    section_ids: list[uuid.UUID] | None = None


class ProvenanceReportRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    scope: str
    document_id: uuid.UUID | None
    covered_versions: list[dict[str, Any]]
    report: dict[str, Any]
    statement_text: str
    statement_template: str
    report_hash: str
    generated_by: str
    created_at: datetime
    stale: bool
    stale_reason: str | None


def _read(db: Session, row: ProvenanceReport) -> ProvenanceReportRead:
    artifact = provenance_store.artifact_for(db, row)
    stale = artifact is not None and artifact.status == ArtifactStatus.stale
    return ProvenanceReportRead(
        id=row.id, project_id=row.project_id, scope=row.scope, document_id=row.document_id,
        covered_versions=row.covered_versions, report=row.report_json, statement_text=row.statement_text,
        statement_template=row.statement_template, report_hash=row.report_hash, generated_by=row.generated_by,
        created_at=row.created_at, stale=stale, stale_reason=artifact.stale_reason if stale else None,
    )


def _report(db: Session, project: Project, report_id: uuid.UUID) -> ProvenanceReport:
    row = db.get(ProvenanceReport, report_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return row


@router.post("/provenance-reports", status_code=status.HTTP_201_CREATED, response_model=ProvenanceReportRead)
def create_report(
    payload: ProvenanceReportCreate,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if payload.scope == "section":
        if payload.document_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A section report needs document_id")
        if payload.section_ids is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A section report takes no section_ids")
        document = db.get(Document, payload.document_id)
        if document is None or document.project_id != project.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        documents = [document]
    else:
        if payload.document_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A manuscript report takes no document_id")
        documents = list(db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.position)).all())
        if payload.section_ids is not None:
            if not payload.section_ids:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="section_ids needs at least one section")
            by_id = {document.id: document for document in documents}
            missing = [str(section_id) for section_id in payload.section_ids if section_id not in by_id]
            if missing:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Document not found: {', '.join(missing)}")
            documents = [by_id[section_id] for section_id in payload.section_ids]

    try:
        row = provenance_store.generate(
            db, project, user=user, scope=payload.scope, documents=documents, document_id=payload.document_id,
        )
    except provenance_store.NothingSaved:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "no_saved_versions", "message": "Save at least one section before making a report"},
        ) from None
    db.commit()
    return _read(db, row)


@router.get("/provenance-reports", response_model=list[ProvenanceReportRead])
def list_reports(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ProvenanceReport).where(ProvenanceReport.project_id == project.id).order_by(ProvenanceReport.created_at.desc())
    ).all()
    return [_read(db, row) for row in rows]


@router.get("/provenance-reports/{report_id}", response_model=None)
def read_report(
    report_id: uuid.UUID,
    format: Literal["json", "md"] = Query("json"),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    row = _report(db, project, report_id)
    if format == "md":
        template_label = row.statement_template
        markdown = pr.to_markdown(row.report_json, row.statement_text, template_label)
        return Response(content=markdown, media_type="text/markdown; charset=utf-8")
    return _read(db, row)
