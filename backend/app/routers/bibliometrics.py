"""Read-only bibliometric views (M3.12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bibliometrics import publication_trends
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Project, Source

router = APIRouter(prefix="/projects/{project_id}/bibliometrics", tags=["bibliometrics"])


@router.get("/publication-trends")
def get_publication_trends(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Count publication years for active sources, without screening-based exclusions."""
    years = db.scalars(
        select(Source.year).where(Source.project_id == project.id, Source.merged_into.is_(None))
    ).all()
    return publication_trends(years)
