from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, current_user, project_access
from app.models import Project, ProjectMember, ProjectRole, User
from app.schemas import MemberInvite, MemberRead

router = APIRouter(prefix="/projects/{project_id}/members", tags=["members"])


def _member_read(member: ProjectMember, user: User) -> MemberRead:
    return MemberRead(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        created_at=member.created_at,
    )


@router.get("", response_model=list[MemberRead])
def list_members(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .order_by(ProjectMember.created_at.asc())
    ).all()
    return [_member_read(member, user) for member, user in rows]


@router.post("", response_model=MemberRead, status_code=status.HTTP_201_CREATED)
def invite_member(
    payload: MemberInvite,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    actor: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Add an existing user by email. People must have signed in once; no email is sent."""
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with that email has signed in yet")
    existing = db.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That user is already a member of this project")
    member = ProjectMember(project_id=project.id, user_id=user.id, role=payload.role)
    db.add(member)
    audit.record(
        db, actor=audit.user_actor(actor), action="member.invited", project_id=project.id,
        payload={"user_id": str(user.id), "role": payload.role.value},
    )
    db.commit()
    db.refresh(member)
    return _member_read(member, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: uuid.UUID,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    actor: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    member = db.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user_id)
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == ProjectRole.owner:
        owners = db.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project.id, ProjectMember.role == ProjectRole.owner
            )
        )
        if owners <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project must keep at least one owner")
    removed_role = member.role.value
    db.delete(member)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(actor), action="member.removed", project_id=project.id,
        payload={"user_id": str(user_id), "role": removed_role},
    )
    if project.owner_id == user_id:
        # owner_id still grants owner access (see project_access), so hand it to a remaining owner.
        successor = db.scalar(
            select(ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id, ProjectMember.role == ProjectRole.owner)
            .order_by(ProjectMember.created_at.asc())
        )
        if successor is None:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project must keep at least one owner")
        project.owner_id = successor
    db.commit()
