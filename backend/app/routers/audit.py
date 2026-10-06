from __future__ import annotations

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import AuditEvent, Project
from app.schemas import AuditEventRead

router = APIRouter(prefix="/projects/{project_id}/audit", tags=["audit"])

# Content that starts with these is treated as a formula by spreadsheet apps.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = ["timestamp", "actor", "action", "model_id", "prompt_version", "payload_json", "id"]


def _project_events(project: Project, action: str | None, actor: str | None):
    query = select(AuditEvent).where(AuditEvent.project_id == project.id)
    if action:
        query = query.where(AuditEvent.action == action)
    if actor:
        query = query.where(AuditEvent.actor == actor)
    return query


def _csv_cell(value) -> str:
    cell = "" if value is None else str(value)
    return "'" + cell if cell.startswith(_FORMULA_PREFIXES) else cell


@router.get("", response_model=list[AuditEventRead])
def list_audit_events(
    action: str | None = Query(default=None, max_length=100),
    actor: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Newest first. Every member can read the project's audit trail."""
    # id breaks timestamp ties so paging is stable. Events written in one transaction share a timestamp.
    query = _project_events(project, action, actor).order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
    return db.scalars(query.limit(limit).offset(offset)).all()


@router.get("/export")
def export_audit_events(
    format: Literal["json", "csv"] = "json",
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """The full trail, oldest first, as a downloadable JSON or CSV file."""
    events = db.scalars(_project_events(project, None, None).order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc())).all()
    filename = f"audit-{project.id}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "json":
        body = json.dumps([AuditEventRead.model_validate(e).model_dump(mode="json") for e in events], indent=2)
        return Response(body, media_type="application/json", headers=headers)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for event in events:
        writer.writerow(
            [
                _csv_cell(event.timestamp.isoformat()),
                _csv_cell(event.actor),
                _csv_cell(event.action),
                _csv_cell(event.model_id),
                _csv_cell(event.prompt_version),
                _csv_cell(json.dumps(event.payload_json, sort_keys=True) if event.payload_json is not None else ""),
                _csv_cell(event.id),
            ]
        )
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
