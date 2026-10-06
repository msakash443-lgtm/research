from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import prisma
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/prisma", tags=["prisma"])


@router.get("")
def get_prisma_flow(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """PRISMA flow numbers computed from the stored search runs (read-only)."""
    return prisma.flow_counts(db, project)
