"""Extractions API (plan M3.2, spec 4 Extraction): store and edit what was pulled out of a paper.

Every write is checked against its extraction schema (`check_extraction`: each non-null field carries
a quote and a page or section). Nothing here marks an extraction verified; that is the verification
step's job (M3.4 span check, M3.6 human review), and an extraction someone verified can't be edited.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, project_schemas
from app.artifacts import refresh_artifact, register_artifact
from app.database import get_db
from app.dependencies import APPROVE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.config import get_settings
from app.extraction_schema import SchemaError, check_extraction, schema_version
from app.extraction_spans import check_evidence_spans
from app.extraction_verification import current_rows, schema_for_row
from app.fulltext import FullTextError, extract_pdf_text
from app.models import Artifact, Extraction, Gate, GateCode, GateStatus, Project, ProjectStage, Source, Task, TaskStatus, User, utcnow
from app.object_storage import ObjectStorageError, get_object_store
from app.task_handlers import AI_EXTRACTION
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}/extractions", tags=["extractions"])

MAX_PAYLOAD_BYTES = 200_000  # fields + evidence of one paper, serialised
LOCKED_MESSAGE = "Gate G4 is approved for this project. Reopen the project at the extraction stage to change an extraction."

EXTRACTION_KIND = "extraction"  # artifact kind for one extraction (X.42)


def _record_extraction(db: Session, project: Project, row: Extraction, actor: str) -> None:
    """One artifact per extraction: a new one is registered; an edited one is current again (it was redone)."""
    artifact = db.scalar(select(Artifact).where(Artifact.project_id == project.id, Artifact.kind == EXTRACTION_KIND, Artifact.ref_id == str(row.id)))
    if artifact is None:
        register_artifact(db, project.id, EXTRACTION_KIND, str(row.id), ProjectStage.extracted, actor=actor)
    else:
        refresh_artifact(db, artifact)


class ExtractionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    schema_name: str = Field(default="default", max_length=80)  # a built-in name or `project:<name>` (M3.1.2)
    fields: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExtractionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExtractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID | None
    source_id: uuid.UUID
    schema_version: str
    fields_json: dict
    evidence_spans: dict  # quotes are text from the paper: show them as text only
    confidence_json: dict | None
    verified_by_human: bool
    verified_by: str | None
    verified_at: datetime | None
    extracted_by: str
    extractor: str
    model_id: str | None
    prompt_version: str | None
    created_at: datetime
    updated_at: datetime


def _locked(db: Session, project: Project) -> bool:
    gate = db.scalar(select(Gate).where(Gate.project_id == project.id, Gate.code == GateCode.G4).with_for_update())
    return gate is not None and gate.status == GateStatus.approved


def _checked(
    db: Session, project: Project, source: Source, schema_name: str,
    fields: dict[str, Any], evidence: dict[str, Any],
) -> str:
    """The `schema_version` to store, or 422 with every problem found."""
    try:
        schema = project_schemas.resolve(db, project.id, schema_name)
    except SchemaError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    payload_bytes = json.dumps(
        {"fields": fields, "evidence": evidence}, default=str, ensure_ascii=False
    ).encode("utf-8")
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Extraction is too large")
    problems = check_extraction(schema, fields, evidence)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problems)
    if not any(value is not None for value in fields.values()):
        return schema_version(schema)
    if not source.fulltext_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This source has no stored full-text PDF for quote verification",
        )
    try:
        settings = get_settings()
        pdf = get_object_store(settings).get(source.fulltext_path)
        fulltext = extract_pdf_text(pdf, max_pages=settings.fulltext_max_pages)
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The source's stored full-text PDF is unavailable for quote verification",
        ) from exc
    except FullTextError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The source's stored full-text PDF could not be read for quote verification",
        ) from exc
    problems = check_evidence_spans(fulltext, fields, evidence)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problems)
    return schema_version(schema)


def _extraction_or_404(db: Session, project: Project, extraction_id: uuid.UUID) -> Extraction:
    row = db.scalar(select(Extraction).where(Extraction.id == extraction_id, Extraction.project_id == project.id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extraction not found")
    return row


@router.get("", response_model=list[ExtractionRead])
def list_extractions(
    source_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """The project's extractions, oldest first; `source_id` narrows to one paper."""
    query = select(Extraction).where(Extraction.project_id == project.id)
    if source_id:
        query = query.where(Extraction.source_id == source_id)
    return list(db.scalars(query.order_by(Extraction.created_at, Extraction.id).limit(limit)))


@router.get("/verification-queue")
def verification_queue(
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> list[dict]:
    items = []
    for source, row in current_rows(db, project):
        stale = False
        critical = []
        if row:
            try:
                critical = [field.key for field in schema_for_row(db, project, row).fields if field.critical]
            except SchemaError:
                stale = True
        items.append({"source_id": str(source.id), "title": source.title,
                      "extraction_id": str(row.id) if row else None,
                      "verified_by_human": row.verified_by_human if row else False,
                      "schema_version": row.schema_version if row else None,
                      "critical_fields": critical, "schema_stale": stale})
    return items


@router.get("/{extraction_id}", response_model=ExtractionRead)
def get_extraction(
    extraction_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    return _extraction_or_404(db, project, extraction_id)


@router.get("/{extraction_id}/pdf")
def extraction_pdf(
    extraction_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> Response:
    row = _extraction_or_404(db, project, extraction_id)
    source = db.scalar(select(Source).where(Source.id == row.source_id, Source.project_id == project.id))
    if source is None or not source.fulltext_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored PDF not found")
    try:
        pdf = get_object_store(get_settings()).get(source.fulltext_path)
    except ObjectStorageError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stored PDF is unavailable") from exc
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="source.pdf"',
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'",
    })


class ExtractionVerificationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_critical_fields: list[str]


@router.post("/{extraction_id}/verify", response_model=ExtractionRead)
def verify_extraction(
    extraction_id: uuid.UUID, payload: ExtractionVerificationIn,
    project: Project = Depends(project_access(WRITE_ROLES | APPROVE_ROLES)),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    """A person confirms every critical field after checking the stored PDF."""
    db.refresh(project, with_for_update=True)
    if _locked(db, project):
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED_MESSAGE)
    row = _extraction_or_404(db, project, extraction_id)
    if row.verified_by_human:
        return row
    if row.id not in {current.id for _, current in current_rows(db, project) if current is not None}:
        raise HTTPException(status.HTTP_409_CONFLICT, "This extraction is no longer the current one for its paper")
    try:
        schema = schema_for_row(db, project, row)
    except SchemaError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    required = {field.key for field in schema.fields if field.critical}
    if len(payload.confirmed_critical_fields) != len(required) or set(payload.confirmed_critical_fields) != required:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Confirm every critical field: {', '.join(sorted(required))}")
    source = db.get(Source, row.source_id)
    if source is None or not source.fulltext_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "This source has no stored full-text PDF for review")
    _checked(db, project, source, schema.name, row.fields_json, row.evidence_spans)
    row.verified_by_human = True
    row.verified_by = audit.user_actor(user)
    row.verified_at = utcnow()
    audit.record(db, actor=row.verified_by, action="extraction.verified", project_id=project.id,
                 payload={"extraction_id": str(row.id), "source_id": str(source.id),
                          "critical_fields": sorted(required)})
    db.commit()
    db.refresh(row)
    return row


class AiExtractionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    schema_name: str = Field(default="default", max_length=80)


class AiExtractionQueued(BaseModel):
    task_id: uuid.UUID
    source_id: uuid.UUID
    status: str


@router.post("/ai", response_model=AiExtractionQueued, status_code=status.HTTP_202_ACCEPTED)
def request_ai_extraction(
    payload: AiExtractionIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue an evidence-grounded AI extraction from the source's stored PDF text."""
    if _locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    source = db.scalar(select(Source).where(
        Source.id == payload.source_id, Source.project_id == project.id, Source.merged_into.is_(None)
    ))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if not source.fulltext_path:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source has no stored full-text PDF")
    try:
        project_schemas.resolve(db, project.id, payload.schema_name)
    except SchemaError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    actor = audit.user_actor(user)
    open_tasks = db.scalars(select(Task).where(
        Task.project_id == project.id, Task.type == AI_EXTRACTION,
        Task.status.in_((TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused)),
    )).all()
    open_task = next((candidate for candidate in open_tasks
                      if (candidate.payload or {}).get("source_id") == str(source.id)
                      and (candidate.payload or {}).get("schema_name") == payload.schema_name), None)
    if open_task is not None:
        task = open_task
    else:
        task = enqueue_task(
            db, project.id, AI_EXTRACTION,
            payload={"project_id": str(project.id), "source_id": str(source.id), "schema_name": payload.schema_name,
                     "requested_by": actor},
            actor=actor,
        )
        audit.record(db, actor=actor, action="extraction.ai_requested", project_id=project.id,
                     payload={"task_id": str(task.id), "source_id": str(source.id), "schema_name": payload.schema_name})
    db.commit()
    return AiExtractionQueued(task_id=task.id, source_id=source.id, status=task.status.value)


@router.post("", response_model=ExtractionRead, status_code=status.HTTP_201_CREATED)
def create_extraction(
    payload: ExtractionIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Record a person's extraction of one paper. It starts unverified."""
    if _locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    source = db.scalar(select(Source).where(Source.id == payload.source_id, Source.project_id == project.id, Source.merged_into.is_(None)))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    version = _checked(db, project, source, payload.schema_name, payload.fields, payload.evidence)
    actor = audit.user_actor(user)
    row = Extraction(
        project_id=project.id, source_id=source.id, schema_version=version, fields_json=payload.fields,
        evidence_spans=payload.evidence, confidence_json=None, verified_by_human=False, extracted_by="human", extractor=actor,
    )
    db.add(row)
    db.flush()
    _record_extraction(db, project, row, actor)
    audit.record(
        db, actor=actor, action="extraction.created", project_id=project.id,
        payload={"extraction_id": str(row.id), "source_id": str(source.id), "schema_version": version,
                 "fields": sorted(k for k, v in payload.fields.items() if v is not None)},
    )
    db.commit()
    db.refresh(row)
    return row


@router.put("/{extraction_id}", response_model=ExtractionRead)
def update_extraction(
    extraction_id: uuid.UUID,
    payload: ExtractionUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace an unverified extraction's fields and evidence, checked against the schema it was made with."""
    if _locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    row = _extraction_or_404(db, project, extraction_id)
    if row.verified_by_human:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This extraction has been verified and can't be edited.")
    name = row.schema_version.partition("@")[0]
    try:
        current_schema = project_schemas.resolve(db, project.id, name)
    except SchemaError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    version = schema_version(current_schema)
    if version != row.schema_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This extraction was made with {row.schema_version}; the schema is now {version}. Create a new extraction instead.",
        )
    source = db.get(Source, row.source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    _checked(db, project, source, name, payload.fields, payload.evidence)
    before = sorted(k for k, v in row.fields_json.items() if v is not None)
    row.fields_json = payload.fields
    row.evidence_spans = payload.evidence
    audit.record(
        db, actor=audit.user_actor(user), action="extraction.updated", project_id=project.id,
        payload={"extraction_id": str(row.id), "source_id": str(row.source_id), "fields_before": before,
                 "fields": sorted(k for k, v in payload.fields.items() if v is not None)},
    )
    _record_extraction(db, project, row, audit.user_actor(user))
    db.commit()
    db.refresh(row)
    return row
