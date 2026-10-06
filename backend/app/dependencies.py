import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Project, ProjectMember, ProjectRole, User


def session_user(request: Request, db: Session) -> User | None:
    """The signed-in user, or None. A session pointing at a missing user is cleared."""
    session_user_id = request.session.get("user_id")
    if not session_user_id:
        return None
    try:
        user = db.get(User, uuid.UUID(session_user_id))
    except (TypeError, ValueError):
        user = None
    if user is None:
        request.session.clear()
    return user


def current_user(
    request: Request,
    x_user_id: uuid.UUID | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    had_session = bool(request.session.get("user_id"))
    user = session_user(request, db)
    if user is not None:
        return user

    # This makes API clients easy to test locally, but is deliberately unavailable in production.
    development = get_settings().environment == "development"
    if development and x_user_id is not None:
        user = db.get(User, x_user_id)
        if user is not None:
            return user

    if had_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session user no longer exists")
    if x_user_id is None or not development:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")


# Who may do what on a project. Roles are not a ladder (a supervisor can approve but
# not edit; a co-author can edit but not approve), so access is a set of allowed roles.
READ_ROLES = frozenset(ProjectRole)
WRITE_ROLES = frozenset({ProjectRole.owner, ProjectRole.co_author})
APPROVE_ROLES = frozenset({ProjectRole.owner, ProjectRole.supervisor})
MANAGE_ROLES = frozenset({ProjectRole.owner})


def project_role(db: Session, project: Project, user: User) -> ProjectRole | None:
    """The user's role on the project, or None. `owner_id` counts as owner even without a member row."""
    if project.owner_id == user.id:
        return ProjectRole.owner
    return db.scalar(
        select(ProjectMember.role).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)
    )


def project_access(allowed: frozenset[ProjectRole] = READ_ROLES):
    """Dependency factory: load the project if the caller holds one of `allowed` roles.

    Non-members get 404 (the project's existence is not revealed); members whose role is
    not allowed get 403. A project's `owner_id` counts as owner even without a member row.
    """

    def dependency(
        project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
    ) -> Project:
        project = db.get(Project, project_id)
        role = project_role(db, project, user) if project is not None else None
        if role is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        if role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role cannot do this on this project")
        return project

    return dependency
