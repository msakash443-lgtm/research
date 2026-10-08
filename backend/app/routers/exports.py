from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, current_user, project_access
from app.models import Project, ResearchContextItem, ResearchRun, Source, User, utcnow
from app.obsidian_export import build_vault, safe_name

router = APIRouter(prefix="/projects/{project_id}/export", tags=["exports"])


@router.get("/obsidian")
def export_obsidian_vault(
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The project as an Obsidian vault (.zip). Verified only: unverified sources and runs that cite
    them are left out, and the counts are recorded in the audit trail."""
    if not get_settings().obsidian_export_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Obsidian export is not enabled")

    sources = db.scalars(select(Source).where(Source.project_id == project.id).order_by(Source.created_at, Source.id)).all()
    context = db.scalars(select(ResearchContextItem).where(ResearchContextItem.project_id == project.id)).all()
    runs = db.scalars(select(ResearchRun).where(ResearchRun.project_id == project.id)).all()
    body, stats = build_vault(project, sources, context, runs, exported_at=utcnow())

    audit.record(db, actor=audit.user_actor(user), action="project.exported", project_id=project.id, payload=stats.as_payload())
    db.commit()

    # Headers are latin-1: keep the download name ASCII (the vault folder inside keeps the full title).
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", safe_name(project.title, "project")).strip("-") or "project"
    filename = f"{filename}-obsidian.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(body, media_type="application/zip", headers=headers)
