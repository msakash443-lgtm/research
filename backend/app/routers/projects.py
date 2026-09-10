import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import current_user, owned_project
from app.models import Project, ResearchContextItem, User
from app.schemas import (
    ContextItemCreate,
    ContextItemRead,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())).all()


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = Project(owner_id=user.id, **payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project: Project = Depends(owned_project)):
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    payload: ProjectUpdate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(project, field, value)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    db.delete(project)
    db.commit()


# ---------------------------------------------------------------------------
# Context items
# ---------------------------------------------------------------------------

@router.get("/{project_id}/context", response_model=list[ContextItemRead])
def list_context(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(ResearchContextItem).where(ResearchContextItem.project_id == project.id)).all()


@router.post("/{project_id}/context", response_model=ContextItemRead, status_code=status.HTTP_201_CREATED)
def add_context(payload: ContextItemCreate, project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    item = ResearchContextItem(project_id=project.id, **payload.model_dump())
    db.add(item)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{project_id}/context/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_context_item(
    item_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(ResearchContextItem).where(
            ResearchContextItem.id == item_id,
            ResearchContextItem.project_id == project.id,
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context item not found")
    db.delete(item)
    db.commit()


