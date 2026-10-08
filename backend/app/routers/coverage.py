"""Coverage matrices (M3.8.2): which method × population × country combinations the extracted papers cover."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.coverage_matrix import build_matrix, papers_for_project
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/coverage-matrix", tags=["coverage"])


@router.get("")
def coverage_matrix(
    rows: str = Query("method"),
    cols: str = Query("population"),
    layer: str | None = Query(None),
    verified_only: bool = Query(False),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Counts per cell, the papers in each, and the empty cells. Read-only; nothing is stored."""
    papers = papers_for_project(db, project, verified_only=verified_only)
    try:
        result = build_matrix(papers, rows, cols, layer or None)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    result["verified_only"] = verified_only
    return result
