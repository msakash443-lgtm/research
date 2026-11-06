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

from app import ai_disclosure_formats, audit
from app import provenance_report as pr
from app import provenance_store
from app.database import get_db
from app.dependencies import READ_ROLES, current_user, project_access, refuse_if_archived
from app.models import ArtifactStatus, Document, DocumentVersion, Project, ProvenanceReport, User

router = APIRouter(prefix="/projects/{project_id}", tags=["provenance"])


class ProvenanceReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["section", "manuscript"]
    document_id: uuid.UUID | None = None
    section_ids: list[uuid.UUID] | None = None
    # X.31.24, owner answer W6 (2026-10-15): which report sections to show. Omitted/null means every section
    # (today's default, backward-compatible with every caller that never sent this field).
    sections: list[Literal["produced", "ai", "pasted", "statement", "limits"]] | None = None


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
    included_sections: list[str]


def _read(db: Session, row: ProvenanceReport) -> ProvenanceReportRead:
    artifact = provenance_store.artifact_for(db, row)
    stale = artifact is not None and artifact.status == ArtifactStatus.stale
    return ProvenanceReportRead(
        id=row.id, project_id=row.project_id, scope=row.scope, document_id=row.document_id,
        covered_versions=row.covered_versions, report=pr.filter_report(row.report_json, row.included_sections),
        statement_text=row.statement_text if "statement" in row.included_sections else "",
        statement_template=row.statement_template, report_hash=row.report_hash, generated_by=row.generated_by,
        created_at=row.created_at, stale=stale, stale_reason=artifact.stale_reason if stale else None,
        included_sections=list(row.included_sections),
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
    refuse_if_archived(project)
    if payload.sections is not None and not payload.sections:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="sections needs at least one section")
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
            included_sections=payload.sections,
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
        statement = row.statement_text if "statement" in row.included_sections else None
        markdown = pr.to_markdown(row.report_json, statement, template_label, included=row.included_sections)
        return Response(content=markdown, media_type="text/markdown; charset=utf-8")
    return _read(db, row)


@router.get("/ai-disclosure-styles")
def ai_disclosure_styles(project: Project = Depends(project_access(READ_ROLES))) -> list[dict[str, str]]:
    """The journal-style wordings a disclosure can be exported in (X.78): id and label, in a stable order."""
    template = ai_disclosure_formats.load_template()
    return [{"id": name, "label": template["styles"][name]["label"]} for name in ai_disclosure_formats.style_names(template)]


@router.get("/provenance-reports/{report_id}/ai-disclosure", response_model=None)
def ai_disclosure(
    report_id: uuid.UUID,
    style: str = Query("generic", max_length=40),
    format: Literal["txt", "json"] = Query("txt"),
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The AI-use disclosure for the sections this report covers, one line per section, in a journal-style wording (X.78).

    Figures are recomputed from the report's frozen section versions (the same ledger the report reads), never from a
    model. A stale report is refused: a section was saved again since, so its figure could understate AI use; make a
    new report first. Audited with ids and counts only."""
    row = _report(db, project, report_id)
    template = ai_disclosure_formats.load_template()
    if style not in template["styles"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown style. Choose one of: {', '.join(ai_disclosure_formats.style_names(template))}")
    artifact = provenance_store.artifact_for(db, row)
    if artifact is not None and artifact.status == ArtifactStatus.stale:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "report_stale", "message": "A section changed after this report. Make a new report, then export the disclosure from it."},
        )
    covered: list[tuple[Document, DocumentVersion]] = []
    for entry in row.covered_versions:
        document = db.get(Document, uuid.UUID(entry["document_id"]))
        version = db.get(DocumentVersion, uuid.UUID(entry["version_id"]))
        if document is None or version is None or document.project_id != project.id:
            raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "section_unavailable", "message": "A section this report covers is no longer available."})
        covered.append((document, version))
    spans, _ = provenance_store.span_rows_for(db, covered)
    rendered = ai_disclosure_formats.render(ai_disclosure_formats.per_section(row.covered_versions, spans), style, template)
    audit.record(
        db, actor=audit.user_actor(user), action="provenance.disclosure_exported", project_id=project.id,
        payload={"report_id": str(row.id), "style": style, "format": format, "sections": len(covered), "template": rendered["template"]},
    )
    db.commit()
    if format == "json":
        return rendered
    return Response(
        content=ai_disclosure_formats.to_text(rendered), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ai-use-disclosure-{style}.txt"'},
    )
