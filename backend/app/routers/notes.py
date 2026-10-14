"""Quick capture (X.31.1) and inbox triage (X.31.2): per-user notes, filed to a project or not.

Notes are the researcher's own words (`body`) or something clipped from elsewhere
(`quoted_text`/`source_url`, kept separate from `body`). Editing a note writes a new
append-only `NoteRevision` and bumps `Note.revision`; nothing already written is
changed. `client_id` (set by the capturing device) makes a retried capture idempotent.

Visibility (Q16): an unfiled note is private to its author. Once filed, every member of that
project may read it, but only the author may change, file, archive or promote it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.ai_item_locks import CONTEXT_ITEM_KIND
from app.database import get_db
from app.dependencies import WRITE_ROLES, current_user, project_role
from app.models import (
    AiItemLock,
    ContextKind,
    Note,
    NoteRevision,
    NoteStatus,
    Project,
    ProjectMember,
    ResearchContextItem,
    Source,
    User,
)
from app.note_suggest import ProjectSummary, SourceSummary, suggest
from app.schemas import (
    ContextItemRead,
    NoteCreate,
    NoteFile,
    NoteLock,
    NotePatch,
    NotePromote,
    NoteRead,
    NoteRevisionRead,
    NoteSuggestionRead,
)

router = APIRouter(prefix="/notes", tags=["notes"])


def _load_own_note(db: Session, note_id: uuid.UUID, user: User) -> Note:
    """The caller's own note, or 404 (existence is not revealed to anyone else)."""
    note = db.get(Note, note_id)
    if note is None or note.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


def _load_readable_note(db: Session, note_id: uuid.UUID, user: User) -> Note:
    """The caller's own note, or a filed note in a project the caller is a member of (read-only), else 404."""
    note = db.get(Note, note_id)
    if note is not None and note.owner_id == user.id:
        return note
    if note is not None and note.project_id is not None:
        project = db.get(Project, note.project_id)
        if project is not None and project_role(db, project, user) is not None:
            return note
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")


def _readable_projects(db: Session, user: User) -> list[Project]:
    """Projects the caller owns or is a member of."""
    member_of = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    return list(db.scalars(select(Project).where((Project.owner_id == user.id) | (Project.id.in_(member_of)))))


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


def _notes_markdown(notes: list[Note]) -> str:
    """One Markdown document. The body is the researcher's words; clipped text is quoted and its source linked, never merged in."""
    parts = ["# Notes export", ""]
    for note in notes:
        parts += [f"## {note.captured_at.isoformat()} · {note.status.value}", ""]
        if note.body:
            parts += [note.body, ""]
        if note.quoted_text:
            parts += [f"> {line}" for line in note.quoted_text.splitlines()] + [""]
        if note.source_url:
            parts += [f"Source: {note.source_url}", ""]
    return "\n".join(parts)


# Declared before /{note_id} so "export" is not read as a note id (X.31.22).
@router.get("/export")
def export_notes(
    fmt: Literal["json", "md"] = Query(default="json", alias="format"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The caller's own notes in every status, newest first: a JSON list, or one Markdown document.
    Only the caller's own notes; a filed note's project readers do not get the export. Audited with format and count."""
    notes = list(db.scalars(select(Note).where(Note.owner_id == user.id).order_by(Note.captured_at.desc())))
    audit.record(db, actor=audit.user_actor(user), action="note.exported", payload={"format": fmt, "count": len(notes)})
    db.commit()
    if fmt == "json":
        return [NoteRead.model_validate(note) for note in notes]
    return PlainTextResponse(_notes_markdown(notes), media_type="text/markdown")


@router.get("/{note_id}", response_model=NoteRead)
def get_note(note_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return NoteRead.model_validate(_load_readable_note(db, note_id, user))


@router.get("/{note_id}/revisions", response_model=list[NoteRevisionRead])
def list_note_revisions(note_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = _load_readable_note(db, note_id, user)
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


@router.post("/{note_id}/file", response_model=NoteRead)
def file_note(
    note_id: uuid.UUID, payload: NoteFile, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """File the note to a project (X.31.2). The caller needs a write role there; otherwise the project
    looks absent, as it does everywhere else. Filing an archived note brings it back into the filed list."""
    note = _load_own_note(db, note_id, user)
    project = db.get(Project, payload.project_id)
    role = project_role(db, project, user) if project is not None else None
    if role is None or role not in WRITE_ROLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    note.project_id = project.id
    note.status = NoteStatus.filed
    audit.record(
        db, actor=audit.user_actor(user), action="note.filed", project_id=project.id,
        payload={"note_id": str(note.id), "project_id": str(project.id)},
    )
    db.commit()
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.post("/{note_id}/promote", response_model=ContextItemRead, status_code=status.HTTP_201_CREATED)
def promote_note(
    note_id: uuid.UUID, payload: NotePromote, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Turn a filed note into a research item in its project (X.31.2). The item keeps a link to the note
    (`promoted_from_note_id`); the note's text is not copied, so the researcher writes the wording.

    If the note is "never send to AI", the new item is locked the same way (an `AiItemLock` row), so no
    research run or AI step is given it either."""
    note = _load_own_note(db, note_id, user)
    if note.status != NoteStatus.filed or note.project_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "note_not_filed", "message": "File the note to a project before promoting it"},
        )
    project = db.get(Project, note.project_id)
    role = project_role(db, project, user) if project is not None else None
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role cannot do this on this project")
    if payload.related_item_id is not None:
        related = db.get(ResearchContextItem, payload.related_item_id)
        if related is None or related.project_id != project.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="related item is not in this project")

    item = ResearchContextItem(
        project_id=project.id,
        kind=ContextKind(payload.kind),
        content=payload.content,
        rationale=payload.rationale,
        created_by=audit.user_actor(user),
        promoted_from_note_id=note.id,
        related_item_id=payload.related_item_id,
    )
    db.add(item)
    db.flush()
    if note.ai_locked:
        db.add(AiItemLock(project_id=project.id, target_kind=CONTEXT_ITEM_KIND, target_id=str(item.id), locked_by=audit.user_actor(user)))
    audit.record(
        db, actor=audit.user_actor(user), action="note.promoted", project_id=project.id,
        payload={"note_id": str(note.id), "item_id": str(item.id), "kind": payload.kind, "ai_locked": note.ai_locked},
    )
    project.touch()
    db.commit()
    db.refresh(item)
    return ContextItemRead.model_validate(item).model_copy(update={"ai_locked": note.ai_locked})


@router.get("/{note_id}/suggestions", response_model=list[NoteSuggestionRead])
def note_suggestions(note_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Non-AI filing and citation hints (X.31.2), for the author only. Scored from the note's words against
    the caller's own projects and their sources (`app/note_suggest.py`). Nothing is sent anywhere."""
    note = _load_own_note(db, note_id, user)
    projects = _readable_projects(db, user)
    project_ids = [project.id for project in projects]
    questions: dict[uuid.UUID, list[str]] = {project_id: [] for project_id in project_ids}
    if project_ids:
        for item in db.scalars(
            select(ResearchContextItem).where(
                ResearchContextItem.project_id.in_(project_ids), ResearchContextItem.kind == ContextKind.question
            )
        ):
            questions[item.project_id].append(item.content)
    summaries = [
        ProjectSummary(
            id=str(project.id), title=project.title, description=project.description or "",
            stage=project.stage.value, question_texts=tuple(questions[project.id]),
        )
        for project in projects
    ]
    source_summaries = []
    if project_ids:
        source_summaries = [
            SourceSummary(id=str(source.id), project_id=str(source.project_id), title=source.title)
            for source in db.scalars(select(Source).where(Source.project_id.in_(project_ids)))
        ]
    text = " ".join(part for part in (note.body, note.quoted_text) if part)
    return [
        NoteSuggestionRead(
            kind=item.kind,
            target_id=item.target_id,
            # A project or stage suggestion names its own project; a source names the project it belongs to.
            project_id=item.target_id if item.kind in ("project", "stage") else item.project_id,
            label=item.label,
            score=item.score,
        )
        for item in suggest(text, summaries, source_summaries)
    ]
