from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, known_items
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.doi import normalize_doi
from app.models import Project, SeedPaper, User

router = APIRouter(prefix="/projects/{project_id}", tags=["seeds"])
MAX_SEEDS = 200


class SeedCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=500)
    doi: str | None = None
    authors: list[str] = Field(default_factory=list, max_length=20)
    year: int | None = Field(default=None, ge=1000, le=2200)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        return normalize_doi(value)

    @field_validator("authors")
    @classmethod
    def _authors(cls, value: list[str]) -> list[str]:
        return [" ".join(a.split())[:200] for a in value if a.strip()]


class SeedRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    doi: str | None
    authors: list | None
    year: int | None
    note: str | None
    created_by: str | None
    created_at: datetime


@router.get("/seeds", response_model=list[SeedRead])
def list_seeds(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(
        select(SeedPaper).where(SeedPaper.project_id == project.id).order_by(SeedPaper.created_at, SeedPaper.id)
    ).all()


@router.post("/seeds", response_model=SeedRead, status_code=status.HTTP_201_CREATED)
def add_seed(
    body: SeedCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if db.scalar(select(func.count()).select_from(SeedPaper).where(SeedPaper.project_id == project.id)) >= MAX_SEEDS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A project can have at most {MAX_SEEDS} seed papers")
    seed = SeedPaper(
        project_id=project.id,
        title=body.title,
        doi=body.doi,
        authors=body.authors or None,
        year=body.year,
        note=body.note,
        created_by=audit.user_actor(user),
    )
    db.add(seed)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A seed paper with this DOI already exists") from None
    audit.record(db, actor=audit.user_actor(user), action="seed.added", project_id=project.id, payload={"seed_id": str(seed.id)})
    db.commit()
    db.refresh(seed)
    return seed


@router.delete("/seeds/{seed_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_seed(
    seed_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    seed = db.scalar(select(SeedPaper).where(SeedPaper.id == seed_id, SeedPaper.project_id == project.id))
    if seed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seed paper not found")
    db.delete(seed)
    audit.record(db, actor=audit.user_actor(user), action="seed.removed", project_id=project.id, payload={"seed_id": str(seed_id)})
    db.commit()


@router.get("/known-items")
def known_item_check(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Which seed papers the project's searches found, and which they missed (read-only)."""
    return known_items.check(db, project)
