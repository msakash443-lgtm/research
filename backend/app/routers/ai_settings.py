"""Project AI controls (X.31.7): how far AI may take part in a project, and which model it uses.

Anyone on the project may read the setting (the header shows it); only the owner changes it, because it decides
what leaves the project. Off means no project text is sent to any AI. The change is audited.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, current_user, project_access
from app.models import AiInvolvement, AiModelRoute, Project, User

router = APIRouter(prefix="/projects/{project_id}/ai-settings", tags=["ai settings"])


class AiSettingsRead(BaseModel):
    ai_involvement: AiInvolvement
    ai_model_route: AiModelRoute
    local_model_configured: bool  # whether the deployment has a local/self-hosted model the "local" route can use


class AiSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai_involvement: AiInvolvement | None = None
    ai_model_route: AiModelRoute | None = None


def _read(project: Project) -> AiSettingsRead:
    settings = get_settings()
    return AiSettingsRead(
        ai_involvement=project.ai_involvement,
        ai_model_route=project.ai_model_route,
        local_model_configured=bool(settings.local_llm_api_base_url and settings.local_llm_model),
    )


@router.get("", response_model=AiSettingsRead)
def get_ai_settings(project: Project = Depends(project_access(READ_ROLES))):
    return _read(project)


@router.put("", response_model=AiSettingsRead)
def set_ai_settings(
    payload: AiSettingsUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Change the project's AI level and/or model route (owner only). Fields left out keep their value."""
    new_involvement = payload.ai_involvement or project.ai_involvement
    new_route = payload.ai_model_route or project.ai_model_route
    if new_route == AiModelRoute.local and not _read(project).local_model_configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No local model is configured on this deployment (LOCAL_LLM_API_BASE_URL and LOCAL_LLM_MODEL), so the project cannot be routed to it.",
        )
    if (new_involvement, new_route) != (project.ai_involvement, project.ai_model_route):
        before = {"ai_involvement": project.ai_involvement.value, "ai_model_route": project.ai_model_route.value}
        project.ai_involvement = new_involvement
        project.ai_model_route = new_route
        project.touch()
        audit.record(
            db, actor=audit.user_actor(user), action="project.ai_settings_changed", project_id=project.id,
            payload={"from": before, "to": {"ai_involvement": new_involvement.value, "ai_model_route": new_route.value}},
        )
        db.commit()
        db.refresh(project)
    return _read(project)
