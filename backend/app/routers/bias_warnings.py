"""Bias and coverage warnings (X.6, spec 7.8)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.bias_warnings import bias_warnings
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/bias-warnings", tags=["bias"])


@router.get("")
def get_bias_warnings(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Database-coverage, citation-count and language warnings, counted from the project's data. Read-only."""
    return {"warnings": bias_warnings(db, project)}
