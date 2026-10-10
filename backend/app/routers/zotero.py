"""Zotero library sync (plan M1.11.2): queue the pull/push, and show what is configured.

`POST …/zotero/pull` queues a `zotero_pull` task, which needs gate G2, so it starts **blocked**
until a person approves G2 (bulk retrieval, like database searches). `POST …/zotero/push` queues
a `zotero_push` task (no gate: it sends verified sources out, it decides nothing and brings
nothing in). Nothing here approves, releases or runs anything, and both are refused with 409
while Zotero sync is not configured on this server (off by default).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, Source, TaskStatus, User
from app.task_handlers import ZOTERO_PULL, ZOTERO_PUSH
from app.task_queue import enqueue_task
from app.zotero import _stored_zotero_key, zotero_configured

router = APIRouter(prefix="/projects/{project_id}/zotero", tags=["zotero"])


class ZoteroStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    configured: bool
    library: str | None  # "user:<Zotero user id>"
    collection_key: str | None
    sources_total: int
    sources_verified: int
    sources_synced: int  # already linked to a Zotero item key


class ZoteroQueued(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str  # "blocked" until gate G2 is approved by a person (pull), else "queued"
    blocked_by_gate: str | None = None


def _require_configured() -> None:
    if not zotero_configured(get_settings()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Zotero sync is not configured on this server. Set ZOTERO_SYNC_ENABLED=true with "
                "ZOTERO_API_KEY and ZOTERO_USER_ID (and ZOTERO_COLLECTION_KEY for pushes) in the server settings."
            ),
        )


def _queue(project: Project, user: User, db: Session, task_type: str) -> ZoteroQueued:
    actor = audit.user_actor(user)
    task = enqueue_task(db, project.id, task_type, {"project_id": str(project.id), "actor": actor}, actor=actor)
    audit.record(
        db, actor=actor,
        action="zotero.pull_requested" if task_type == ZOTERO_PULL else "zotero.push_requested",
        project_id=project.id,
        payload={"task_id": str(task.id), "status": task.status.value,
                 "blocked_by_gate": task.blocked_by_gate.value if task.blocked_by_gate else None},
    )
    db.commit()
    return ZoteroQueued(
        task_id=str(task.id), status=task.status.value,
        blocked_by_gate=task.blocked_by_gate.value if task.blocked_by_gate else None,
    )


@router.get("", response_model=ZoteroStatusRead)
def zotero_status(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    sources = db.scalars(select(Source).where(
        Source.project_id == project.id, Source.merged_into.is_(None))).all()
    return ZoteroStatusRead(
        enabled=settings.zotero_sync_enabled,
        configured=zotero_configured(settings),
        library=f"user:{settings.zotero_user_id}" if (settings.zotero_user_id or "").strip() else None,
        collection_key=settings.zotero_collection_key,
        sources_total=len(sources),
        sources_verified=sum(1 for s in sources if s.metadata_verified),
        sources_synced=sum(1 for s in sources if _stored_zotero_key(s.source_ids)),
    )


@router.post("/pull", response_model=ZoteroQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_zotero_pull(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue the pull: the Zotero library's items enter as unverified sources (behind gate G2)."""
    _require_configured()
    return _queue(project, user, db, ZOTERO_PULL)


@router.post("/push", response_model=ZoteroQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_zotero_push(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue the push: this project's verified sources become Zotero items (no gate)."""
    _require_configured()
    return _queue(project, user, db, ZOTERO_PUSH)
