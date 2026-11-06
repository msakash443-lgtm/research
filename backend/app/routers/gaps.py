"""Project-scoped gap mining and scholar curation (M3.9)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.gap_miner import validate_gap_evidence, validate_gap_type, validate_project_evidence
from app.models import Extraction, Gap, Project, Source, User

router = APIRouter(prefix="/projects/{project_id}/gaps", tags=["gaps"])


class GapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=30)
    statement: str = Field(min_length=1, max_length=10000)
    supporting_paper_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    matrix_cell_ids: list[str] = Field(default_factory=list, max_length=100)
    future_research: list[dict] = Field(default_factory=list, max_length=100)
    priority: int = Field(default=0, ge=0, le=100)


class GapPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int | None = Field(default=None, ge=0, le=100)
    selected: bool | None = None


class FutureResearchRead(BaseModel):
    statement: str
    source_id: uuid.UUID
    extraction_id: uuid.UUID


class GapRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    type: str
    statement: str
    supporting_paper_ids: list[uuid.UUID]
    matrix_cell_ids: list[str]
    future_research: list[FutureResearchRead]
    priority: int
    selected: bool
    created_by: str
    created_at: datetime


def _gap(db: Session, project: Project, gap_id: uuid.UUID) -> Gap:
    gap = db.scalar(select(Gap).where(Gap.id == gap_id, Gap.project_id == project.id))
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gap not found")
    return gap


@router.get("", response_model=list[GapRead])
def list_gaps(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(Gap).where(Gap.project_id == project.id).order_by(Gap.priority.desc(), Gap.created_at)).all()


@router.post("", response_model=GapRead, status_code=status.HTTP_201_CREATED)
def create_gap(payload: GapIn, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        validate_gap_type(payload.type)
        validate_gap_evidence([str(item) for item in payload.supporting_paper_ids], payload.matrix_cell_ids)
        validate_project_evidence(db, project, payload.supporting_paper_ids, payload.matrix_cell_ids)
        for item in payload.future_research:
            if set(item) != {"statement", "source_id", "extraction_id"} or not item["statement"].strip():
                raise ValueError("future_research entries need statement, source_id, and extraction_id")
            source_id, extraction_id = uuid.UUID(item["source_id"]), uuid.UUID(item["extraction_id"])
            if db.scalar(select(Extraction.id).where(Extraction.id == extraction_id, Extraction.project_id == project.id, Extraction.source_id == source_id)) is None:
                raise ValueError("future_research provenance must reference a project extraction")
            if db.scalar(select(Source.id).where(Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None))) is None:
                raise ValueError("future_research provenance must reference an active project source")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    gap = Gap(project_id=project.id, type=payload.type, statement=payload.statement.strip(),
              supporting_paper_ids=[str(item) for item in payload.supporting_paper_ids],
              matrix_cell_ids=payload.matrix_cell_ids, future_research=payload.future_research,
              priority=payload.priority, created_by=str(user.id))
    db.add(gap)
    db.flush()
    audit.record(db, actor=str(user.id), action="gap.created", project_id=project.id,
                 payload={"gap_id": str(gap.id), "type": gap.type, "evidence_count": len(payload.supporting_paper_ids) + len(payload.matrix_cell_ids)})
    db.commit()
    return _gap(db, project, gap.id)


@router.patch("/{gap_id}", response_model=GapRead)
def curate_gap(gap_id: uuid.UUID, payload: GapPatch, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    gap = _gap(db, project, gap_id)
    if payload.priority is not None:
        gap.priority = payload.priority
    if payload.selected is not None:
        gap.selected = payload.selected
    audit.record(db, actor=str(user.id), action="gap.curated", project_id=project.id,
                 payload={"gap_id": str(gap.id), "priority": gap.priority, "selected": gap.selected})
    db.commit()
    return _gap(db, project, gap.id)
