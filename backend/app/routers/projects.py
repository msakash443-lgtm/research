import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.agent.context import select_agent_context
from app.database import get_db
from app.gates import new_gates
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, ProjectMember, ProjectRole, ResearchContextItem, User
from app.schemas import ContextItemCreate, ContextItemRead, ProjectCreate, ProjectRead

router = APIRouter(prefix="/projects", tags=["projects"])


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
def list_context(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    # Oldest first, so questions read in the order they were written; id keeps same-second ties stable.
    items = db.scalars(
        select(ResearchContextItem)
        .where(ResearchContextItem.project_id == project.id)
        .order_by(ResearchContextItem.created_at.asc(), ResearchContextItem.id.asc())
    ).all()
    sent = {item.id for item in select_agent_context(items)}
    return [
        ContextItemRead.model_validate(item).model_copy(update={"used_by_agent": item.id in sent})
        for item in items
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
