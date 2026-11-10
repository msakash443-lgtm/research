"""Research objectives: what the research is for, amended with history and retired, never deleted (X.193)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, ResearchObjective, ResearchQuestion, User, utcnow
from app.schemas import ObjectiveCreate, ObjectiveRead, ObjectiveUpdate

router = APIRouter(prefix="/projects", tags=["objectives"])


def _require_question_in_project(db: Session, project: Project, question_id: uuid.UUID | None) -> None:
    if question_id is None:
        return
    found = db.scalar(select(ResearchQuestion.id).where(ResearchQuestion.id == question_id, ResearchQuestion.project_id == project.id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The linked research question is not in this project")


def _live_objective(db: Session, project: Project, objective_id: uuid.UUID) -> ResearchObjective:
    objective = db.scalar(
        select(ResearchObjective).where(ResearchObjective.id == objective_id, ResearchObjective.project_id == project.id)
    )
    if objective is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Objective not found")
    if objective.retired_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This objective is retired; it can no longer be changed")
    return objective


@router.get("/{project_id}/objectives", response_model=list[ObjectiveRead])
def list_objectives(
    include_retired: bool = Query(default=False),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Objectives, oldest first. Retired objectives are left out unless `?include_retired=true` (X.193)."""
    query = select(ResearchObjective).where(ResearchObjective.project_id == project.id)
    if not include_retired:
        query = query.where(ResearchObjective.retired_at.is_(None))
    return db.scalars(query.order_by(ResearchObjective.created_at.asc(), ResearchObjective.id.asc())).all()


@router.post("/{project_id}/objectives", response_model=ObjectiveRead, status_code=status.HTTP_201_CREATED)
def add_objective(
    payload: ObjectiveCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_question_in_project(db, project, payload.research_question_id)
    objective = ResearchObjective(project_id=project.id, created_by=audit.user_actor(user), **payload.model_dump())
    db.add(objective)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="objective.added", project_id=project.id,
        payload={"objective_id": str(objective.id)},
    )
    project.touch()
    db.commit()
    db.refresh(objective)
    return objective


@router.patch("/{project_id}/objectives/{objective_id}", response_model=ObjectiveRead)
def amend_objective(
    objective_id: uuid.UUID,
    payload: ObjectiveUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Correct an objective. The earlier statement and rationale are kept in its history; the audit event
    records which fields changed, never the text (X.193)."""
    objective = _live_objective(db, project, objective_id)
    changes = {}
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if field == "statement" and value is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The statement cannot be blank")
        if value != getattr(objective, field):
            changes[field] = value
    if not changes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nothing changed")
    if "research_question_id" in changes:
        _require_question_in_project(db, project, changes["research_question_id"])
    actor = audit.user_actor(user)
    objective.history = [
        *(objective.history or []),
        {"statement": objective.statement, "rationale": objective.rationale, "changed_by": actor, "changed_at": utcnow().isoformat()},
    ]
    for field, value in changes.items():
        setattr(objective, field, value)
    audit.record(
        db, actor=actor, action="objective.amended", project_id=project.id,
        payload={"objective_id": str(objective.id), "fields": sorted(changes)},
    )
    project.touch()
    db.commit()
    db.refresh(objective)
    return objective


@router.post("/{project_id}/objectives/{objective_id}/retire", response_model=ObjectiveRead)
def retire_objective(
    objective_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Take an objective out of use. It is hidden from lists but never deleted (X.193)."""
    objective = _live_objective(db, project, objective_id)
    actor = audit.user_actor(user)
    objective.retired_at = utcnow()
    objective.retired_by = actor
    audit.record(
        db, actor=actor, action="objective.retired", project_id=project.id,
        payload={"objective_id": str(objective.id)},
    )
    project.touch()
    db.commit()
    db.refresh(objective)
    return objective
