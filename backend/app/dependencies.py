import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Project, User


def current_user(
    request: Request,
    x_user_id: uuid.UUID | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    session_user_id = request.session.get("user_id")
    if session_user_id:
        try:
            user = db.get(User, uuid.UUID(session_user_id))
        except (TypeError, ValueError):
            user = None
        if user is not None:
            return user
        request.session.clear()

    # This makes API clients easy to test locally, but is deliberately unavailable in production.
    if get_settings().environment == "development" and x_user_id is not None:
        user = db.scalar(select(User).where(User.id == x_user_id))
        if user is not None:
            return user

    if session_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session user no longer exists")
    if x_user_id is not None and get_settings().environment != "development":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if x_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")


def owned_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == user.id))
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
