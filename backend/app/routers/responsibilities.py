"""The responsibility matrix (X.226). Separate from authorisation (`ProjectRole`) and from CRediT contributions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, current_user, project_access
from app.models import Project, ProjectMember, ProjectResponsibility, User
from app.responsibilities import AREAS, LEVELS, InvalidResponsibility, validate_assignments

router = APIRouter(prefix="/projects/{project_id}/responsibilities", tags=["responsibilities"])


class AssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID
    level: str


class AreaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignments: list[AssignmentIn] = Field(default_factory=list, max_length=50)


class AssignmentRead(BaseModel):
    area: str
    user_id: uuid.UUID
    name: str
    level: str


class MatrixRead(BaseModel):
    areas: list[dict]
    levels: list[str]
    assignments: list[AssignmentRead]


def _matrix(db: Session, project: Project) -> MatrixRead:
    rows = db.execute(
        select(ProjectResponsibility, User)
        .join(User, User.id == ProjectResponsibility.user_id)
        .where(ProjectResponsibility.project_id == project.id)
        .order_by(ProjectResponsibility.area, case((ProjectResponsibility.level == "lead", 0), else_=1), ProjectResponsibility.created_at)
    ).all()
    return MatrixRead(
        areas=[{"code": code, "label": label} for code, label in AREAS], levels=list(LEVELS),
        assignments=[AssignmentRead(area=r.area, user_id=r.user_id, name=u.display_name or u.email, level=r.level) for r, u in rows],
    )


@router.get("", response_model=MatrixRead)
def get_responsibilities(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Who leads or supports each area of the work. Informational: it grants no access and is not a CRediT role."""
    return _matrix(db, project)


@router.put("/{area}", response_model=MatrixRead)
def set_area(
    area: str,
    payload: AreaUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    actor: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace the people on one area with exactly `assignments` (an empty list clears it). People must be members of
    this project. Audited with ids and levels only. Changes no permission and no CRediT role."""
    try:
        pairs = validate_assignments(area, [(str(a.user_id), a.level) for a in payload.assignments])
    except InvalidResponsibility as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    member_ids = {
        str(uid) for uid in db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))
    } | {str(project.owner_id)}
    outsiders = [uid for uid, _ in pairs if uid not in member_ids]
    if outsiders:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Only members of this project can hold a responsibility.")
    existing = db.scalars(select(ProjectResponsibility).where(
        ProjectResponsibility.project_id == project.id, ProjectResponsibility.area == area)).all()
    before = sorted((str(r.user_id), r.level) for r in existing)
    for row in existing:
        db.delete(row)
    db.flush()
    who = audit.user_actor(actor)
    for uid, level in pairs:
        db.add(ProjectResponsibility(project_id=project.id, area=area, user_id=uuid.UUID(uid), level=level, assigned_by=who))
    audit.record(
        db, actor=who, action="responsibility.set", project_id=project.id,
        payload={"area": area, "from": before, "to": sorted(pairs)},
    )
    db.commit()
    return _matrix(db, project)
