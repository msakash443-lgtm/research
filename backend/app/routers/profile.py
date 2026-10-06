from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, current_user, project_access
from app.discipline import ProfileError, available_profiles, effective_settings, load_profile
from app.models import Project, User
from app.schemas import DisciplineProfileRead, ProfileUpdate, ProjectProfileRead

# Two routers so the shared catalogue of profiles is not nested under a project.
catalogue = APIRouter(prefix="/discipline-profiles", tags=["discipline profiles"])
router = APIRouter(prefix="/projects/{project_id}/profile", tags=["discipline profiles"])


@catalogue.get("", response_model=list[DisciplineProfileRead])
def list_profiles(_user: User = Depends(current_user)):
    """The shipped discipline profiles a project can choose from."""
    return [load_profile(name) for name in available_profiles()]


def _view(project: Project) -> ProjectProfileRead:
    try:
        effective = effective_settings(project.discipline, project.config_json)
        label = load_profile(project.discipline).label if project.discipline else None
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{exc}. Choose a profile again.") from exc
    return ProjectProfileRead(discipline=project.discipline, label=label, overrides=project.config_json or {}, effective=effective)


@router.get("", response_model=ProjectProfileRead)
def get_profile(project: Project = Depends(project_access(READ_ROLES))):
    """The project's profile, its own overrides, and the settings that actually apply."""
    return _view(project)


@router.put("", response_model=ProjectProfileRead)
def set_profile(
    payload: ProfileUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Choose the discipline profile and any overrides (owner only: these decide databases and reporting norms)."""
    overrides = payload.overrides.model_dump(exclude_none=True)
    before = {"discipline": project.discipline, "overrides": project.config_json or {}}
    project.discipline = payload.discipline
    project.config_json = overrides or None
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="project.profile_changed", project_id=project.id,
        payload={"from": before, "to": {"discipline": project.discipline, "overrides": overrides}},
    )
    db.commit()
    return _view(project)
