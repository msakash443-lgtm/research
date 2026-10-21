"""AI accuracy against this project's own people (X.80). Read-only; see `app.accuracy_report` for the definitions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import accuracy_report
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/accuracy-report", tags=["accuracy"])


@router.get("")
def get_accuracy_report(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Recall and precision of the AI's screening suggestions and extractions against what people decided here."""
    return accuracy_report.project_report(db, project)
