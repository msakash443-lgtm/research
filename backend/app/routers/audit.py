from __future__ import annotations

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.database import SessionLocal, get_db
from app.dependencies import READ_ROLES, current_user, project_access, project_role
from app.models import AuditEvent, Project, ProjectRole, User
from app.schemas import AuditEventRead

router = APIRouter(prefix="/projects/{project_id}/audit", tags=["audit"])

# Content that starts with these is treated as a formula by spreadsheet apps.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = ["timestamp", "actor", "action", "model_id", "prompt_version", "payload_json", "id"]

SCREENING_PREFIX = "screening."  # owner decision 2026-10-19 (M2.3.2/M2.3.3): non-owners see only their own


def _project_events(project: Project, action: str | None, actor: str | None, *, viewer_actor: str, is_owner: bool):
    query = select(AuditEvent).where(AuditEvent.project_id == project.id)
    if not is_owner:
        # A screening-decision audit event (who decided what, when) is per-reviewer blinded, like the
        # decision itself; everything else (gates, sources, searches, ...) is unaffected.
        query = query.where(or_(~AuditEvent.action.like(f"{SCREENING_PREFIX}%"), AuditEvent.actor == viewer_actor))
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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Newest first. Every member can read the project's audit trail, except screening-decision events
    (`screening.*`), which a non-owner sees only their own (owner decision 2026-10-19, M2.3.2/M2.3.3)."""
    # id breaks timestamp ties so paging is stable. Events written in one transaction share a timestamp.
    is_owner = project_role(db, project, user) == ProjectRole.owner
    query = _project_events(project, action, actor, viewer_actor=audit.user_actor(user), is_owner=is_owner)
    return db.scalars(query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(limit).offset(offset)).all()


@router.get("/export")
def export_audit_events(
    format: Literal["json", "csv"] = "json",
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The full trail, oldest first, as a downloadable JSON or CSV file. Screening-decision events
    (`screening.*`) are a non-owner's own only, same as the list endpoint (2026-10-19 decision)."""
    is_owner = project_role(db, project, user) == ProjectRole.owner
    query = _project_events(project, None, None, viewer_actor=audit.user_actor(user), is_owner=is_owner)
    query = query.order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc())
    filename = f"audit-{project.id}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    # Rows are streamed from a session of their own: the request session belongs to the access check,
    # and FastAPI closes a yield-dependency session before a streamed body is sent.
    if format == "json":
        return StreamingResponse(_json_chunks(query), media_type="application/json", headers=headers)
    return StreamingResponse(_csv_chunks(query), media_type="text/csv; charset=utf-8", headers=headers)


EXPORT_BATCH_ROWS = 500


def _stream_events(query):
    with SessionLocal() as stream_db:
        yield from stream_db.scalars(query.execution_options(yield_per=EXPORT_BATCH_ROWS))


def _json_chunks(query):
    # Same bytes as json.dumps(list, indent=2), produced one event at a time.
    yield "["
    first = True
    for event in _stream_events(query):
        item = json.dumps(AuditEventRead.model_validate(event).model_dump(mode="json"), indent=2)
        yield ("" if first else ",") + "\n  " + item.replace("\n", "\n  ")
        first = False
    yield "]" if first else "\n]"


def _csv_row(event: AuditEvent) -> list[str]:
    return [
        _csv_cell(event.timestamp.isoformat()),
        _csv_cell(event.actor),
        _csv_cell(event.action),
        _csv_cell(event.model_id),
        _csv_cell(event.prompt_version),
        _csv_cell(json.dumps(event.payload_json, sort_keys=True) if event.payload_json is not None else ""),
        _csv_cell(event.id),
    ]


def _csv_chunks(query):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    yield buffer.getvalue()
    for event in _stream_events(query):
        buffer.seek(0)
        buffer.truncate()
        writer.writerow(_csv_row(event))
        yield buffer.getvalue()
