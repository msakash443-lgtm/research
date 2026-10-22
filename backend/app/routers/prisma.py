from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import discipline, prisma, reporting_checklists
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/prisma", tags=["prisma"])


@router.get("")
def get_prisma_flow(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """PRISMA flow numbers computed from the stored search runs (read-only)."""
    return prisma.flow_counts(db, project)


@router.get("/checklist")
def get_reporting_checklist(project: Project = Depends(project_access(READ_ROLES))) -> dict[str, Any]:
    """Return the project's canonical checklist items and the authoritative source link.

    Full publisher prose is intentionally not duplicated in the application. Items are
    canonical identifiers/headings; the source link is the authority for the complete
    wording and version.
    """
    guideline = discipline.effective_settings(project.discipline, project.config_json).reporting_guideline
    items = reporting_checklists.checklist_for(guideline)
    source = reporting_checklists.CHECKLIST_SOURCES.get(guideline) if guideline else None
    return {
        "guideline": guideline,
        "shipped": items is not None and source is not None,
        "source": asdict(source) if source else None,
        "items": [asdict(item) for item in items or ()],
    }
