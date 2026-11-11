"""Similarity check of a manuscript section against this project's sources (X.31.14).

POST queues a `similarity_check` task for the section's latest saved version and returns 202; the task (`similarity_task.py`)
runs the pure rules in `app/similarity.py` and stores one append-only `SimilarityCheck` row. Nothing is sent outside the server.
Only the `project_sources` provider exists; an institutional provider reports `not_configured` until its licence
is confirmed (Q17, X.31.15).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai_disclosure, audit, credit, manuscript_export, manuscript_guard, similarity_providers
from app.config import get_settings
from app.discipline import ProfileError, effective_settings
from app.database import get_db
from app.lookups import project_document_or_404
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import AnalysisRun, AuditEvent, Document, DocumentVersion, Project, ProjectMember, SimilarityCheck, Source, User
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

    document = project_document_or_404(db, project, payload.document_id)
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
        statement = statement.where(SimilarityCheck.document_id == project_document_or_404(db, project, document_id).id)
    rows = db.scalars(statement.order_by(SimilarityCheck.created_at.desc(), SimilarityCheck.id)).all()
    return [_read(db, row) for row in rows]


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LATEX_MEDIA_TYPE = "application/x-latex; charset=utf-8"
EXPORT_EXTENSIONS = {"md": "md", "json": "json", "docx": "docx", "bibtex": "bib", "latex": "tex", "overleaf": "zip"}


class ManuscriptStatementsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    data_availability_statement: str | None
    conflict_of_interest_statement: str | None
    ethics_statement: str | None
    ai_use_disclosure: str


def _ai_use_disclosure(db: Session, project_id: uuid.UUID) -> str:
    """Generated from the audit log (M6.6): every recorded action that carried a `model_id`."""
    rows = db.execute(
        select(AuditEvent.action, AuditEvent.model_id, AuditEvent.prompt_version).where(AuditEvent.project_id == project_id)
    ).all()
    return ai_disclosure.generate([ai_disclosure.AiAuditAction(action=a, model_id=m, prompt_version=p) for a, m, p in rows])


def _display_name(user: User) -> str:
    return user.display_name or user.email


def _credit_contributions(db: Session, project_id: uuid.UUID) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rows = db.execute(
        select(User, ProjectMember.credit_roles)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.created_at.asc())
    ).all()
    return tuple((_display_name(user), tuple(roles or [])) for user, roles in rows)


class CreditContributorRead(BaseModel):
    member_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    roles: list[str]


class CreditRolesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(default_factory=list)


@router.get("/manuscript/credit", response_model=list[CreditContributorRead])
def list_credit_contributions(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Every project member and their CRediT contribution roles, for the manuscript's "Author Contributions"
    statement. AI is never listed here: only `ProjectMember` rows (human users) can hold roles."""
    rows = db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .order_by(ProjectMember.created_at.asc())
    ).all()
    return [
        CreditContributorRead(member_id=member.id, user_id=user.id, name=_display_name(user), roles=list(member.credit_roles or []))
        for member, user in rows
    ]


@router.put("/manuscript/credit/{member_id}", response_model=CreditContributorRead)
def set_credit_contribution(
    member_id: uuid.UUID,
    payload: CreditRolesUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Sets one project member's CRediT roles (standard 14-term taxonomy, `app.credit.CREDIT_ROLES`); an unknown
    role name is refused (422), never silently dropped. Audited with which roles were set, not free text, so this
    carries no researcher-authored prose."""
    member = db.scalar(select(ProjectMember).where(ProjectMember.id == member_id, ProjectMember.project_id == project.id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Member not found")
    try:
        member.credit_roles = credit.validate_roles(payload.roles)
    except credit.InvalidCreditRole as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    audit.record(
        db, actor=audit.user_actor(user), action="manuscript.credit_set", project_id=project.id,
        payload={"member_id": str(member.id), "roles": member.credit_roles},
    )
    db.commit()
    db.refresh(member)
    return CreditContributorRead(member_id=member.id, user_id=member.user_id, name=_display_name(member.user), roles=list(member.credit_roles))


class ManuscriptStatementsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_availability_statement: str | None = Field(default=None, max_length=4000)
    conflict_of_interest_statement: str | None = Field(default=None, max_length=4000)
    ethics_statement: str | None = Field(default=None, max_length=4000)


@router.get("/manuscript/statements", response_model=ManuscriptStatementsRead)
def get_manuscript_statements(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """The researcher's own submission-statement text (M6.5): data availability, conflict of interest, ethics.
    Unset fields are `null`; `GET /manuscript/export` shows a bracketed to-complete marker for those instead.
    `ai_use_disclosure` (M6.6) is generated from the audit log, never researcher-written."""
    return ManuscriptStatementsRead(
        data_availability_statement=project.data_availability_statement,
        conflict_of_interest_statement=project.conflict_of_interest_statement,
        ethics_statement=project.ethics_statement,
        ai_use_disclosure=_ai_use_disclosure(db, project.id),
    )


@router.put("/manuscript/statements", response_model=ManuscriptStatementsRead)
def set_manuscript_statements(
    payload: ManuscriptStatementsUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Overwrites all three statements with the researcher's own words; nothing here calls a model. Audited with
    which fields were set (never the text itself, same as every other audited free-text field in this app)."""
    changes = payload.model_dump()
    for field, value in changes.items():
        setattr(project, field, value)
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="manuscript.statements_updated", project_id=project.id,
        payload={"fields_set": sorted(field for field, value in changes.items() if value)},
    )
    db.commit()
    db.refresh(project)
    return ManuscriptStatementsRead(
        data_availability_statement=project.data_availability_statement,
        conflict_of_interest_statement=project.conflict_of_interest_statement,
        ethics_statement=project.ethics_statement,
        ai_use_disclosure=_ai_use_disclosure(db, project.id),
    )


def _citation_style(project: Project) -> str | None:
    try:
        return effective_settings(project.discipline, project.config_json).citation_style
    except ProfileError:
        # A broken/renamed discipline profile is a profile-config problem, not a reason to block export; fall
        # back to the default style the same way an unset discipline does.
        return None


@router.get("/manuscript/export", response_model=None)
def export_manuscript(
    fmt: Literal["md", "docx", "latex", "json", "bibtex", "overleaf"] = Query(default="md", alias="format"),
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The latest saved version of every section, in section order, as Markdown, Word, LaTeX, JSON or BibTeX
    (X.31.13, M6.5). Citations and the reference list use the project's discipline citation style
    (`app.discipline.effective_settings`, `app.csl_styles`); an unset discipline keeps the plain author-year format
    used before M6.5. The three submission statements (data availability, conflict of interest, ethics) are appended
    to md/docx/latex, bracketed as "to complete" where the researcher has not written one yet (`app.ethics_pack`'s
    convention); JSON carries the raw, possibly-null values instead.

    Sections never saved are left out (their count is in `X-Unsaved-Sections`); with nothing saved the export holds only
    the title, so a read never fails on an empty manuscript. Citations to unverified sources, and inserted numbers that
    do not resolve against a project analysis run (M6.3), are marked and left out of the references and the BibTeX
    (plan rule 24). Audited with the format and the section count. Nothing here calls a model."""
    documents = db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.position, Document.created_at)).all()
    saved: list[tuple[Document, DocumentVersion]] = []
    for document in documents:
        version = latest_version(db, document.id)
        if version is not None:
            saved.append((document, version))
    sections = [manuscript_export.Section(title=document.title, content=version.content_json) for document, version in saved]
    blockers = manuscript_guard.export_blockers(db, project.id, [(document, version.content_json) for document, version in saved])
    if blockers:
        audit.record(
            db, actor=audit.user_actor(user), action="manuscript.export_blocked", project_id=project.id,
            payload={"format": fmt, "citations": [{"document_id": b.document_id, "kind": b.kind, "subject": b.subject} for b in blockers]},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": "Export refused: one or more saved sections cite a source, or carry an inserted number, that is not verified or not known to this project.",
                "citations": [
                    {"document_id": b.document_id, "document_title": b.document_title, "kind": b.kind, "subject": b.subject, "message": b.message,
                     **({"status": "unverifiable", "verification": b.verification} if b.verification is not None else {})}
                    for b in blockers
                ],
            },
        )
    cited_ids: set[str] = set()
    run_ids: set[str] = set()
    for section in sections:
        cited_ids.update(manuscript_export.cited_source_ids(section.content))
        run_ids.update(manuscript_export.stat_run_ids(section.content))
    source_rows: dict[str, Source] = {}
    if cited_ids:
        wanted = [uuid.UUID(i) for i in cited_ids if _is_uuid(i)]
        source_rows = {str(s.id): s for s in db.scalars(select(Source).where(Source.project_id == project.id, Source.id.in_(wanted)))}
    sources = {
        key: manuscript_export.CitedSource(
            id=key, title=row.title, authors=tuple(row.authors or ()), year=row.year, venue=row.venue, doi=row.doi,
            verified=bool(row.metadata_verified), verification_method=row.verification_method,
        )
        for key, row in source_rows.items()
    }
    runs: dict[str, Any] = {}
    if run_ids:
        wanted_runs = [uuid.UUID(i) for i in run_ids if _is_uuid(i)]
        runs = {str(r.id): r.output_json for r in db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project.id, AnalysisRun.id.in_(wanted_runs)))}
    style = _citation_style(project)
    references = manuscript_export.references(sections, sources, style)
    statements = manuscript_export.Statements(
        data_availability=project.data_availability_statement,
        conflict_of_interest=project.conflict_of_interest_statement,
        ethics=project.ethics_statement,
        ai_use_disclosure=_ai_use_disclosure(db, project.id),
        credit=_credit_contributions(db, project.id),
    )

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
        return Response(
            manuscript_export.to_markdown(project.title, sections, sources, runs, style=style, statements=statements),
            media_type="text/markdown; charset=utf-8", headers=headers,
        )
    if fmt == "docx":
        return Response(
            manuscript_export.to_docx(project.title, sections, sources, runs, style=style, statements=statements),
            media_type=DOCX_MEDIA_TYPE, headers=headers,
        )
    if fmt == "latex":
        return Response(
            manuscript_export.to_latex(project.title, sections, sources, runs, style=style, statements=statements),
            media_type=LATEX_MEDIA_TYPE, headers=headers,
        )
    if fmt == "bibtex":
        records = [
            {k: v for k, v in {"title": source_rows[s.id].title, "authors": source_rows[s.id].authors, "year": s.year, "doi": s.doi,
                               "url": source_rows[s.id].url, "venue": s.venue, "source_type": source_rows[s.id].source_type}.items() if v}
            for s in references
        ]
        return Response(to_bibtex(records), media_type="text/plain; charset=utf-8", headers=headers)
    if fmt == "overleaf":
        latex = manuscript_export.to_latex(project.title, sections, sources, runs, style=style, statements=statements)
        records = [
            {k: v for k, v in {"title": source_rows[s.id].title, "authors": source_rows[s.id].authors, "year": s.year, "doi": s.doi,
                               "url": source_rows[s.id].url, "venue": s.venue, "source_type": source_rows[s.id].source_type}.items() if v}
            for s in references
        ]
        bibtex = to_bibtex(records)
        readme = (
            "# Overleaf package\n\n"
            "Upload this ZIP to Overleaf and compile `manuscript.tex` with pdfLaTeX.\n"
            "The bibliography is embedded as `references.bib`; all references passed the project's citation guard.\n"
        ).encode("utf-8")
        entries = {
            "README.md": readme,
            "manuscript.tex": latex.encode("utf-8"),
            "references.bib": bibtex.encode("utf-8"),
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, body in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, body)
        return Response(
            buffer.getvalue(), media_type="application/zip",
            headers={**headers, "Content-Disposition": 'attachment; filename="overleaf-package.zip"'},
        )
    body = manuscript_export.to_json(
        project.title,
        [
            {"document_id": str(d.id), "title": d.title, "position": d.position, "version": v.number,
             "content_json": v.content_json, "content_text": v.content_text}
            for d, v in saved
        ],
        references,
        statements=statements,
    )
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/json", headers=headers)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
