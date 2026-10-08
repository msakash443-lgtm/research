from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import LlmUsage, Project

router = APIRouter(prefix="/projects/{project_id}/usage", tags=["usage"])


class PurposeUsage(BaseModel):
    purpose: str
    calls: int
    tokens: int


class UsageRead(BaseModel):
    tokens_used: int
    token_budget: int | None  # None = no limit
    calls: int
    calls_without_token_counts: int
    by_purpose: list[PurposeUsage]


@router.get("", response_model=UsageRead)
def project_usage(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """What the model has used for this project, and the budget (if any) that stops further calls."""
    rows = db.execute(
        select(LlmUsage.purpose, func.count(), func.coalesce(func.sum(LlmUsage.total_tokens), 0), func.count(LlmUsage.total_tokens))
        .where(LlmUsage.project_id == project.id)
        .group_by(LlmUsage.purpose)
        .order_by(LlmUsage.purpose)
    ).all()
    budget = get_settings().project_token_budget
    return UsageRead(
        tokens_used=sum(int(r[2]) for r in rows),
        token_budget=budget or None,
        calls=sum(r[1] for r in rows),
        calls_without_token_counts=sum(r[1] - r[3] for r in rows),
        by_purpose=[PurposeUsage(purpose=r[0], calls=r[1], tokens=int(r[2])) for r in rows],
    )
