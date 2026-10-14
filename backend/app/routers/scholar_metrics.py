"""Read-only scholar-outcome metrics for a project (X.20)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import AiSuggestion, Document, Project
from app.scholar_metrics import scholar_metrics

router = APIRouter(prefix="/projects/{project_id}/scholar-metrics", tags=["scholar-metrics"])


@router.get("")
def get_scholar_metrics(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Human corrections per 100 AI outputs, manuscript readiness, and time saved (not recorded yet)."""
    suggestions = db.scalars(
        select(AiSuggestion).where(AiSuggestion.project_id == project.id).options(selectinload(AiSuggestion.events))
    ).all()
    documents = db.scalars(
        select(Document).where(Document.project_id == project.id).order_by(Document.position)
    ).all()
    return scholar_metrics(
        ([event.kind for event in suggestion.events] for suggestion in suggestions),
        ((document.title, document.current_version_id is not None) for document in documents),
    )
