import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit, research_types
from app.agent.context import select_agent_context
from app.ai_item_locks import CONTEXT_ITEM_KIND, locked_ids
from app.database import get_db
from app.gates import new_gates
from app.dependencies import MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import ContextKind, Gate, GateStatus, Project, ProjectMember, ProjectRole, ResearchContextItem, ProjectStage, User
from app.schemas import ContextItemCreate, ContextItemRead, ProjectCreate, ProjectRead, ResearchTypeRead, ResearchTypeUpdate

router = APIRouter(prefix="/projects", tags=["projects"])
catalogue = APIRouter(prefix="/research-types", tags=["research types"])
@catalogue.get("", response_model=list[ResearchTypeRead])
def list_research_types(user: User = Depends(current_user)):
    return [research_types.load_research_type(name) for name in research_types.available_research_types()]



@router.put("/{project_id}/research-type", response_model=ResearchTypeRead)
def update_research_type(
    payload: ResearchTypeUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if project.stage != ProjectStage.idea:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Research type can only change at the idea stage")
    if db.scalar(select(Gate.id).where(Gate.project_id == project.id, Gate.status == GateStatus.approved)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Research type cannot change after a gate is approved")
    try:
        selected = research_types.load_research_type(payload.name)
    except research_types.ResearchTypeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    project.research_type = selected.name
    project.stage_plan_json = selected.model_dump(mode="json")
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="project.research_type_changed", project_id=project.id,
        payload={"research_type": selected.name, "version": selected.version},
    )
    db.commit()
    return selected


@router.get("", response_model=list[ProjectRead])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    member_of = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    return db.scalars(
        select(Project).where(or_(Project.owner_id == user.id, Project.id.in_(member_of))).order_by(Project.updated_at.desc())
    ).all()


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = Project(owner_id=user.id, **payload.model_dump())
    project.members.append(ProjectMember(user_id=user.id, role=ProjectRole.owner))
    project.gates.extend(new_gates())
    db.add(project)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="project.created", project_id=project.id,
        payload={"title": project.title},
    )
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project: Project = Depends(project_access(READ_ROLES))):
    return project


@router.get("/{project_id}/context", response_model=list[ContextItemRead])
def list_context(
    kind: ContextKind | None = Query(default=None),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """All context items, oldest first. `?kind=decision` is the decision log (X.31.2): the same list, filtered.
    The agent flags are computed over the whole list, so filtering never changes what a run is given."""
    # Oldest first, so questions read in the order they were written; id keeps same-second ties stable.
    items = db.scalars(
        select(ResearchContextItem)
        .where(ResearchContextItem.project_id == project.id)
        .order_by(ResearchContextItem.created_at.asc(), ResearchContextItem.id.asc())
    ).all()
    locked = locked_ids(db, project.id, CONTEXT_ITEM_KIND)
    sent = {item.id for item in select_agent_context(items, locked_ids=locked)}
    return [
        ContextItemRead.model_validate(item).model_copy(update={"used_by_agent": item.id in sent, "ai_locked": str(item.id) in locked})
        for item in items
        if kind is None or item.kind == kind
    ]


@router.post("/{project_id}/context", response_model=ContextItemRead, status_code=status.HTTP_201_CREATED)
def add_context(
    payload: ContextItemCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = ResearchContextItem(project_id=project.id, created_by=audit.user_actor(user), **payload.model_dump())
    db.add(item)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="context.added", project_id=project.id,
        payload={"item_id": str(item.id), "kind": item.kind.value},
    )
    project.touch()
    db.commit()
    db.refresh(item)
    return item
