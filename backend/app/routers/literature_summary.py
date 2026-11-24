"""The deterministic literature-review summary (X.200)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import literature_summary
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.extraction_schema import SchemaError
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/literature-summary", tags=["literature-summary"])


@router.get("")
def get_literature_summary(
    format: str = Query("json", pattern="^(json|md)$"),
    schema_name: str = Query("default", max_length=80),
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
):
    try:
        summary = literature_summary.build(db, project, schema_name)
    except SchemaError as exc:
        raise HTTPException(422, str(exc)) from exc
    if format == "md":
        return Response(literature_summary.markdown(summary, project.title), media_type="text/markdown; charset=utf-8")
    return summary
