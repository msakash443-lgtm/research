"""Replication package download (plan X.8.1, spec 13.6 #6): the project's audit/replication record in one zip."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, current_user, project_access
from app.models import Project, User, utcnow
from app.replication_export import build_package

router = APIRouter(prefix="/projects/{project_id}/export", tags=["exports"])


@router.get("/replication")
def export_replication_package(
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Every project table as JSON + CSV, PRISMA counts and a manifest of file hashes (see `app/replication_export.py`).
    Any member may export it, as with the audit trail; the export is recorded in the audit trail after it is built."""
    actor = audit.user_actor(user)
    exported_at = utcnow()
    body, summary = build_package(db, project, exported_by=actor, exported_at=exported_at)
    audit.record(db, actor=actor, action="project.replication_exported", project_id=project.id, payload=summary)
    db.commit()
    filename = f"replication-{project.id}-{exported_at.strftime('%Y%m%dT%H%M%SZ')}.zip"  # one name per download
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(body, media_type="application/zip", headers=headers)
