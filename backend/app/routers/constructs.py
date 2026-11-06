"""Construct and theory inventory API (M3.7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Construct, Project, Theory, TheoryConstruct, User

router = APIRouter(prefix="/projects/{project_id}", tags=["constructs"])


class ConstructIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=20_000)
    measures: list[Any] = Field(default_factory=list, max_length=100)
    author: str = Field(min_length=1, max_length=200)
    jingle_jangle_flags: list[str] = Field(default_factory=list, max_length=20)


class ConstructRead(ConstructIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    created_by: str
    created_at: datetime
    updated_at: datetime


class TheoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20_000)
    construct_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class TheoryRead(TheoryIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    created_by: str
    created_at: datetime
    updated_at: datetime


def _construct(db: Session, project: Project, item_id: uuid.UUID) -> Construct:
    item = db.scalar(select(Construct).where(Construct.id == item_id, Construct.project_id == project.id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Construct not found")
    return item


def _theory(db: Session, project: Project, item_id: uuid.UUID) -> Theory:
    item = db.scalar(select(Theory).where(Theory.id == item_id, Theory.project_id == project.id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Theory not found")
    return item


@router.get("/constructs", response_model=list[ConstructRead])
def list_constructs(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return list(db.scalars(select(Construct).where(Construct.project_id == project.id).order_by(Construct.name, Construct.author)))


@router.post("/constructs", response_model=ConstructRead, status_code=status.HTTP_201_CREATED)
def create_construct(payload: ConstructIn, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = Construct(project_id=project.id, created_by=str(user.id), **payload.model_dump())
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A construct with this name and author already exists") from exc
    audit.record(db, actor=audit.user_actor(user), action="construct.created", project_id=project.id, payload={"construct_id": str(item.id)})
    db.commit()
    db.refresh(item)
    return item


@router.get("/theories", response_model=list[TheoryRead])
def list_theories(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return list(db.scalars(select(Theory).where(Theory.project_id == project.id).order_by(Theory.name)))


@router.post("/theories", response_model=TheoryRead, status_code=status.HTTP_201_CREATED)
def create_theory(payload: TheoryIn, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    known = set(db.scalars(select(Construct.id).where(Construct.project_id == project.id)).all())
    if any(item not in known for item in payload.construct_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Every construct must belong to this project")
    values = payload.model_dump(exclude={"construct_ids"})
    item = Theory(project_id=project.id, created_by=str(user.id), **values)
    db.add(item)
    try:
        db.flush()
        db.add_all(TheoryConstruct(project_id=project.id, theory_id=item.id, construct_id=construct_id)
                   for construct_id in payload.construct_ids)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A theory with this name already exists") from exc
    audit.record(db, actor=audit.user_actor(user), action="theory.created", project_id=project.id, payload={"theory_id": str(item.id)})
    db.commit()
    db.refresh(item)
    return item
