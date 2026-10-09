from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, current_user, project_access
from app.models import LlmUsage, Project, User

router = APIRouter(prefix="/projects/{project_id}/usage", tags=["usage"])


class PurposeUsage(BaseModel):
    purpose: str
    calls: int
    tokens: int


class UsageRead(BaseModel):
    tokens_used: int
    token_budget: int | None  # effective budget for this project (override if set, else the global default); None = no limit
    budget_override: int | None  # this project's own override, if the owner set one; None = using the global default
    default_budget: int | None  # the global PROJECT_TOKEN_BUDGET setting; None = no limit
    calls: int
    calls_without_token_counts: int
    by_purpose: list[PurposeUsage]


class BudgetOverrideUpdate(BaseModel):
    override: int | None = Field(default=None, ge=0)  # None clears the override (use the global default)


def _usage_read(project: Project, db: Session) -> UsageRead:
    rows = db.execute(
        select(LlmUsage.purpose, func.count(), func.coalesce(func.sum(LlmUsage.total_tokens), 0), func.count(LlmUsage.total_tokens))
        .where(LlmUsage.project_id == project.id)
        .group_by(LlmUsage.purpose)
        .order_by(LlmUsage.purpose)
    ).all()
    default_budget = get_settings().project_token_budget
    effective = default_budget if project.token_budget_override is None else project.token_budget_override
    return UsageRead(
        tokens_used=sum(int(r[2]) for r in rows),
        token_budget=effective or None,
        budget_override=project.token_budget_override,
        default_budget=default_budget or None,
        calls=sum(r[1] for r in rows),
        calls_without_token_counts=sum(r[1] - r[3] for r in rows),
        by_purpose=[PurposeUsage(purpose=r[0], calls=r[1], tokens=int(r[2])) for r in rows],
    )


@router.get("", response_model=UsageRead)
def project_usage(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """What the model has used for this project, and the budget (if any) that stops further calls."""
    return _usage_read(project, db)


@router.put("/budget", response_model=UsageRead)
def set_budget_override(
    payload: BudgetOverrideUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Set or clear this project's own token-budget override (owner only; 0 means no limit for this project)."""
    before = project.token_budget_override
    project.token_budget_override = payload.override
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="project.budget_override_changed", project_id=project.id,
        payload={"from": before, "to": payload.override},
    )
    db.commit()
    db.refresh(project)
    return _usage_read(project, db)
