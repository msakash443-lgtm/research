"""Quick capture (X.31.1): per-user notes, not scoped to a project.

Notes are the researcher's own words (`body`) or something clipped from elsewhere
(`quoted_text`/`source_url`, kept separate from `body`). Editing a note writes a new
append-only `NoteRevision` and bumps `Note.revision`; nothing already written is
changed. `client_id` (set by the capturing device) makes a retried capture idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import current_user
from app.models import Note, NoteRevision, NoteStatus, User
from app.schemas import NoteCreate, NoteLock, NotePatch, NoteRead, NoteRevisionRead

router = APIRouter(prefix="/notes", tags=["notes"])


def _load_own_note(db: Session, note_id: uuid.UUID, user: User) -> Note:
    """The caller's own note, or 404 (existence is not revealed to anyone else)."""
    note = db.get(Note, note_id)
    if note is None or note.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def capture_note(
    payload: NoteCreate, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Save a capture. Posting the same `client_id` again returns the existing note (200, not
    created again), so a retried offline submit can't double-save."""
    def existing_note() -> Note | None:
        return db.scalar(select(Note).where(Note.owner_id == user.id, Note.client_id == payload.client_id))

    existing = existing_note()
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return NoteRead.model_validate(existing)

    note = Note(
        owner_id=user.id,
        project_id=None,
        client_id=payload.client_id,
        kind=payload.kind,
        body=payload.body,
        quoted_text=payload.quoted_text,
        source_url=payload.source_url,
        locator=payload.locator,
        ai_locked=payload.ai_locked,
        revision=1,
        captured_at=payload.captured_at or datetime.now(timezone.utc),
        device=payload.device,
    )
    try:
        db.add(note)
        db.flush()
        db.add(NoteRevision(note_id=note.id, revision=1, body=note.body, quoted_text=note.quoted_text, edited_by=audit.user_actor(user)))
        audit.record(
            db, actor=audit.user_actor(user), action="note.created",
            payload={"note_id": str(note.id), "kind": note.kind.value},
        )
        db.commit()
    except IntegrityError:
        # A concurrent retry of the same capture won the insert: hand back its note, as above.
        db.rollback()
        existing = existing_note()
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        return NoteRead.model_validate(existing)
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.get("", response_model=list[NoteRead])
def list_notes(
    status_filter: NoteStatus | None = Query(default=None, alias="status"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Note).where(Note.owner_id == user.id).order_by(Note.captured_at.desc())
    if status_filter is not None:
        query = query.where(Note.status == status_filter)
    return [NoteRead.model_validate(note) for note in db.scalars(query)]


@router.get("/{note_id}", response_model=NoteRead)
def get_note(note_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return NoteRead.model_validate(_load_own_note(db, note_id, user))


@router.get("/{note_id}/revisions", response_model=list[NoteRevisionRead])
def list_note_revisions(note_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = _load_own_note(db, note_id, user)
    revisions = db.scalars(select(NoteRevision).where(NoteRevision.note_id == note.id).order_by(NoteRevision.revision))
    return [NoteRevisionRead.model_validate(revision) for revision in revisions]


@router.patch("/{note_id}", response_model=NoteRead)
def edit_note(
    note_id: uuid.UUID, payload: NotePatch, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Edit the body. Writes a new revision; the old one stays in `/revisions` forever.
    `base_revision` must match the note's current revision, or the edit is refused (409) with
    the server's copy, so two edits made offline from different revisions never silently overwrite
    each other."""
    note = _load_own_note(db, note_id, user)
    if payload.base_revision != note.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "note_conflict", "current": NoteRead.model_validate(note).model_dump(mode="json")},
        )
    note.revision += 1
    note.body = payload.body
    db.add(NoteRevision(note_id=note.id, revision=note.revision, body=note.body, quoted_text=note.quoted_text, edited_by=audit.user_actor(user)))
    audit.record(db, actor=audit.user_actor(user), action="note.edited", payload={"note_id": str(note.id), "revision": note.revision})
    db.commit()
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.post("/{note_id}/archive", response_model=NoteRead)
def archive_note(note_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = _load_own_note(db, note_id, user)
    note.status = NoteStatus.archived
    audit.record(db, actor=audit.user_actor(user), action="note.archived", payload={"note_id": str(note.id)})
    db.commit()
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.post("/{note_id}/lock", response_model=NoteRead)
def lock_note(
    note_id: uuid.UUID, payload: NoteLock, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Set or clear 'never send to AI' for this note. Checked server-side by every AI action
    (X.31.7/X.31.11), never trusted from the client at request time."""
    note = _load_own_note(db, note_id, user)
    note.ai_locked = payload.ai_locked
    audit.record(
        db, actor=audit.user_actor(user), action="note.locked",
        payload={"note_id": str(note.id), "ai_locked": payload.ai_locked},
    )
    db.commit()
    db.refresh(note)
    return NoteRead.model_validate(note)
