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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, project_schemas
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.extraction_schema import SchemaError, check_extraction, schema_version
from app.models import Extraction, Gate, GateCode, GateStatus, Project, Source, User

router = APIRouter(prefix="/projects/{project_id}/extractions", tags=["extractions"])

MAX_PAYLOAD_BYTES = 200_000  # fields + evidence of one paper, serialised
LOCKED_MESSAGE = "Gate G4 is approved for this project. Reopen the project at the extraction stage to change an extraction."


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
    source_id: uuid.UUID
    schema_version: str
    fields_json: dict
    evidence_spans: dict  # quotes are text from the paper: show them as text only
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
    return db.scalar(select(Gate.status).where(Gate.project_id == project.id, Gate.code == GateCode.G4)) == GateStatus.approved


def _checked(db: Session, project: Project, schema_name: str, fields: dict[str, Any], evidence: dict[str, Any]) -> str:
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


@router.get("/{extraction_id}", response_model=ExtractionRead)
def get_extraction(
    extraction_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    return _extraction_or_404(db, project, extraction_id)


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
    version = _checked(db, project, payload.schema_name, payload.fields, payload.evidence)
    actor = audit.user_actor(user)
    row = Extraction(
        project_id=project.id, source_id=source.id, schema_version=version, fields_json=payload.fields,
        evidence_spans=payload.evidence, verified_by_human=False, extracted_by="human", extractor=actor,
    )
    db.add(row)
    db.flush()
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
    _checked(db, project, name, payload.fields, payload.evidence)
    before = sorted(k for k, v in row.fields_json.items() if v is not None)
    row.fields_json = payload.fields
    row.evidence_spans = payload.evidence
    audit.record(
        db, actor=audit.user_actor(user), action="extraction.updated", project_id=project.id,
        payload={"extraction_id": str(row.id), "source_id": str(row.source_id), "fields_before": before,
                 "fields": sorted(k for k, v in payload.fields.items() if v is not None)},
    )
    db.commit()
    db.refresh(row)
    return row
