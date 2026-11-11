"""Per-item "never for this item" AI lock (X.46.3): the one check every AI step asks before it is given an item.

A lock is a (project, target kind, target id) row. A note's lock is `Note.ai_locked`, read here so a note and any
other item answer the same question: `is_locked`. A suggestion about a record's screening is about the source
itself, so the `screening` kind is the `source` kind here: locking either locks the record for every AI step.

Callers filter locked items out *before* building a prompt (`locked_ids`), and `suggestion_store.create_suggestion`
refuses a locked target as a second line, so a producer that forgets cannot store a suggestion about a locked item.
This module never commits; it rides the caller's transaction like `audit.record`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app.models import AiItemLock, Note

NOTE_KIND = "note"
# Suggestion target kinds that are really another kind of item: a screening suggestion's target id is the source's id.
KIND_ALIASES = {"screening": "source"}
MAX_KIND_CHARS = 40
MAX_ID_CHARS = 100
CONTEXT_ITEM_KIND = "context_item"


class AiItemLocked(ValueError):
    """The item is locked: no AI step may be given it."""


def canonical_kind(kind: str) -> str:
    normalized = kind.strip().lower()
    return KIND_ALIASES.get(normalized, normalized)


def is_locked(db: Session, project_id: uuid.UUID, kind: str, target_id: str | uuid.UUID) -> bool:
    kind, target = canonical_kind(kind), str(target_id)
    if kind == NOTE_KIND:
        try:
            note = db.get(Note, uuid.UUID(target))
        except ValueError:
            return False
        return bool(note is not None and note.ai_locked)
    return db.scalar(
        select(AiItemLock.id).where(AiItemLock.project_id == project_id, AiItemLock.target_kind == kind, AiItemLock.target_id == target)
    ) is not None


def locked_ids(db: Session, project_id: uuid.UUID, kind: str) -> frozenset[str]:
    """Every locked id of one kind in the project, for filtering a list before a prompt is built. Not for notes
    (they belong to a person, not a project): ask `is_locked` per note."""
    kind = canonical_kind(kind)
    if kind == NOTE_KIND:
        raise ValueError("notes are locked per note; use is_locked")
    return frozenset(
        db.scalars(select(AiItemLock.target_id).where(AiItemLock.project_id == project_id, AiItemLock.target_kind == kind))
    )


def locked_uuids(db: Session, project_id: uuid.UUID, kind: str) -> list[uuid.UUID]:
    """`locked_ids` as UUIDs, for a `NOT IN` filter on a table keyed by UUID. An id that is not a UUID can't match a row."""
    found = []
    for value in locked_ids(db, project_id, kind):
        try:
            found.append(uuid.UUID(value))
        except ValueError:
            continue
    return found


def ensure_not_locked(db: Session, project_id: uuid.UUID, kind: str, target_id: str | uuid.UUID) -> None:
    if is_locked(db, project_id, kind, target_id):
        raise AiItemLocked(f"{canonical_kind(kind)} {target_id} is set to 'never for this item', so no AI step may be given it")


def set_lock(db: Session, project_id: uuid.UUID, kind: str, target_id: str, actor: str) -> bool:
    """Lock an item. Returns False (and writes nothing) if it was already locked."""
    kind = canonical_kind(kind)
    if is_locked(db, project_id, kind, target_id):
        return False
    db.add(AiItemLock(project_id=project_id, target_kind=kind, target_id=str(target_id), locked_by=actor))
    audit.record(
        db, actor=actor, action="ai_lock.set", project_id=project_id,
        payload={"target_kind": kind, "target_id": str(target_id)},
    )
    db.flush()
    return True


def clear_lock(db: Session, project_id: uuid.UUID, kind: str, target_id: str, actor: str) -> bool:
    """Unlock an item. Returns False (and writes nothing) if it was not locked."""
    kind = canonical_kind(kind)
    removed = db.execute(
        delete(AiItemLock).where(AiItemLock.project_id == project_id, AiItemLock.target_kind == kind, AiItemLock.target_id == str(target_id))
    ).rowcount
    if not removed:
        return False
    audit.record(
        db, actor=actor, action="ai_lock.cleared", project_id=project_id,
        payload={"target_kind": kind, "target_id": str(target_id)},
    )
    return True
