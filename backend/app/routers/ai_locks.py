"""'Never for this item' (X.46.3): lock one item so no AI step is ever given it, and unlock it again.

Anyone who may edit the project can set or clear a lock; everyone on it can read the list. Every change is
audited. Locking also moves the item's pending AI suggestions to `locked`, so none is left waiting for a
decision. The AI steps read the same lock (`app/ai_item_locks.py`) and skip a locked item on the server, so
the lock holds whatever the browser does. A note's lock stays on the note (`PATCH /api/notes/{id}/lock`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, suggestion_store, suggestions
from app.ai_item_locks import (
    CONTEXT_ITEM_KIND, MAX_ID_CHARS, MAX_KIND_CHARS, NOTE_KIND, canonical_kind, clear_lock, set_lock,
)
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import AiItemLock, Project, ResearchContextItem, Source, User

router = APIRouter(prefix="/projects/{project_id}/ai-locks", tags=["ai locks"])

# Kinds whose ids are rows in this project: the item must exist here before it can be locked.
_ROW_KINDS = {"source": Source, CONTEXT_ITEM_KIND: ResearchContextItem}


class AiLockRead(BaseModel):
    target_kind: str
    target_id: str
    locked_by: str
    created_at: datetime


class AiLockChanged(BaseModel):
    target_kind: str
    target_id: str
    locked: bool
    changed: bool  # False when the item was already in that state
    suggestions_locked: int = 0


def _target(db: Session, project: Project, target_kind: str, target_id: str) -> tuple[str, str]:
    """Validate and normalise (kind, id). A gate can't be a target (nothing about a gate is ever AI-proposed)."""
    try:
        kind = canonical_kind(suggestions.validate_target(target_kind))
    except suggestions.ForbiddenTarget as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if kind == NOTE_KIND:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A note's lock is set on the note itself (PATCH /api/notes/{id}/lock).")
    target = target_id.strip()
    if not target or len(kind) > MAX_KIND_CHARS or len(target) > MAX_ID_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A lock needs a target kind and id of reasonable length.")
    model = _ROW_KINDS.get(kind)
    if model is not None:
        try:
            row_id = uuid.UUID(target)
        except ValueError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item in this project.") from None
        row = db.get(model, row_id)
        if row is None or row.project_id != project.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item in this project.")
        target = str(row_id)
    return kind, target


@router.get("", response_model=list[AiLockRead])
def list_locks(
    target_kind: str | None = None,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    query = select(AiItemLock).where(AiItemLock.project_id == project.id).order_by(AiItemLock.created_at, AiItemLock.id)
    if target_kind:
        query = query.where(AiItemLock.target_kind == canonical_kind(target_kind))
    return [
        AiLockRead(target_kind=lock.target_kind, target_id=lock.target_id, locked_by=lock.locked_by, created_at=lock.created_at)
        for lock in db.scalars(query)
    ]


@router.put("/{target_kind}/{target_id}", response_model=AiLockChanged)
def lock_item(
    target_kind: str,
    target_id: str,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Never send this item to AI. Idempotent: locking a locked item changes nothing."""
    kind, target = _target(db, project, target_kind, target_id)
    actor = audit.user_actor(user)
    created = set_lock(db, project.id, kind, target, actor)
    moved = suggestion_store.lock_target(db, project.id, kind, target, actor) if created else 0
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # someone locked it at the same moment: it is locked either way
        return AiLockChanged(target_kind=kind, target_id=target, locked=True, changed=False)
    return AiLockChanged(target_kind=kind, target_id=target, locked=True, changed=created, suggestions_locked=moved)


@router.delete("/{target_kind}/{target_id}", response_model=AiLockChanged)
def unlock_item(
    target_kind: str,
    target_id: str,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Let AI steps be given this item again. Suggestions already set to 'never for this item' stay as they were."""
    kind, target = _target(db, project, target_kind, target_id)
    removed = clear_lock(db, project.id, kind, target, audit.user_actor(user))
    db.commit()
    return AiLockChanged(target_kind=kind, target_id=target, locked=False, changed=removed)
