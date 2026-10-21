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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.ai_item_locks import CONTEXT_ITEM_KIND
from app.database import get_db
from app.dependencies import WRITE_ROLES, current_user, project_role, refuse_if_archived
from app.models import (
    AiItemLock,
    ContextKind,
    NoteAttachment,
    SourceExcerpt,
    Note,
    NoteRevision,
    NoteKind,
    NoteStatus,
    Project,
    ProjectMember,
    ResearchContextItem,
    Source,
    excerpt_hash,
    User,
)
from app.object_storage import get_object_store, validate_key
from app.note_suggest import ProjectSummary, SourceSummary, suggest
from app.schemas import (
    ContextItemRead,
    NoteCreate,
    NoteFile,
    HighlightCreate,
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


def _load_own_note_for_change(db: Session, note_id: uuid.UUID, user: User) -> Note:
    """Like `_load_own_note`, but a note filed to an archived project is read-only (X.4.7.6)."""
    note = _load_own_note(db, note_id, user)
    project = db.get(Project, note.project_id) if note.project_id is not None else None
    if project is not None:
        refuse_if_archived(project)
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
    """Save a capture. Posting the same `client_id` again with the same content returns the existing note
    (200, not created again), so a retried offline submit can't double-save. Posting the same `client_id`
    with *different* content (X.31.16: an offline draft that diverged from what was saved elsewhere under
    the same client id) is refused as 409 `note_conflict`, carrying the stored note as `current`, so the
    caller can offer a keep-mine/keep-theirs/keep-both choice (offlineOutbox.js::resolveConflict) instead of
    silently keeping whichever write happened to land first."""
    def existing_note() -> Note | None:
        return db.scalar(select(Note).where(Note.owner_id == user.id, Note.client_id == payload.client_id))

    def same_content(note: Note) -> bool:
        return note.body == payload.body and note.quoted_text == payload.quoted_text

    def conflict(note: Note) -> HTTPException:
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "note_conflict",
                "message": "This capture was already saved with different content under the same client id.",
                "current": NoteRead.model_validate(note).model_dump(mode="json"),
            },
        )

    existing = existing_note()
    if existing is not None:
        if same_content(existing):
            response.status_code = status.HTTP_200_OK
            return NoteRead.model_validate(existing)
        raise conflict(existing)

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
        # A concurrent retry of the same capture won the insert: hand back its note (or 409, as above).
        db.rollback()
        existing = existing_note()
        if existing is None:
            raise
        if same_content(existing):
            response.status_code = status.HTTP_200_OK
            return NoteRead.model_validate(existing)
        raise conflict(existing) from None
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.get("", response_model=list[NoteRead])
def list_notes(
    status_filter: NoteStatus | None = Query(default=None, alias="status"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Note).where(Note.owner_id == user.id).order_by(Note.captured_at.desc(), Note.created_at.desc(), Note.id.desc())
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



def _existing_note(db: Session, user: User, client_id: str) -> Note | None:
    return db.scalar(select(Note).where(Note.owner_id == user.id, Note.client_id == client_id))


@router.post("/highlight", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def capture_highlight(
    payload: HighlightCreate,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Save a source-linked PDF highlight while keeping the researcher's comment separate."""
    existing = _existing_note(db, user, payload.client_id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return NoteRead.model_validate(existing)

    source = db.get(Source, payload.source_id)
    project = db.get(Project, source.project_id) if source is not None else None
    if source is None or project is None or project_role(db, project, user) not in WRITE_ROLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    refuse_if_archived(project)
    if not source.fulltext_path:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source has no stored PDF")

    actor = audit.user_actor(user)
    quoted_text = payload.quoted_text.strip()
    locator = payload.locator.strip() if payload.locator else None
    try:
        excerpt = SourceExcerpt(
            source_id=source.id,
            content=quoted_text,
            locator=locator,
            content_hash=excerpt_hash(quoted_text),
            created_by=actor,
        )
        db.add(excerpt)
        db.flush()
        note = Note(
            owner_id=user.id,
            project_id=project.id,
            client_id=payload.client_id,
            kind=NoteKind.highlight,
            body=payload.body or "",
            quoted_text=quoted_text,
            source_id=source.id,
            excerpt_id=excerpt.id,
            locator=locator,
            revision=1,
            captured_at=payload.captured_at or datetime.now(timezone.utc),
        )
        db.add(note)
        db.flush()
        db.add(NoteRevision(note_id=note.id, revision=1, body=note.body, quoted_text=quoted_text, edited_by=actor))
        audit.record(db, actor=actor, action="source.excerpt_added", project_id=project.id,
                     payload={"source_id": str(source.id), "excerpt_id": str(excerpt.id), "content_hash": excerpt.content_hash})
        audit.record(db, actor=actor, action="note.created", project_id=project.id,
                     payload={"note_id": str(note.id), "kind": note.kind.value, "source_id": str(source.id), "excerpt_id": str(excerpt.id)})
        project.touch()
        db.commit()
    except IntegrityError:
        # A concurrent retry of the same highlight won the insert: hand back its note (the excerpt rolls back with ours).
        db.rollback()
        existing = _existing_note(db, user, payload.client_id)
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        return NoteRead.model_validate(existing)
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.post("/voice", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def capture_voice(
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Capture audio bytes only; no transcription or speech-recognition path exists in V1."""
    existing = db.scalar(select(Note).where(Note.owner_id == user.id, Note.client_id == client_id))
    if existing is not None:
        return NoteRead.model_validate(existing)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    extensions = {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
    }
    extension = extensions.get(media_type)
    if extension is None:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Voice note must be WebM, Ogg, WAV, MP3, or M4A")
    max_bytes = get_settings().object_storage_max_bytes
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Voice note is too large")
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Voice note cannot be empty")
    note = Note(
        owner_id=user.id, project_id=None, client_id=client_id, kind=NoteKind.voice, body="",
        revision=1, captured_at=datetime.now(timezone.utc),
    )
    store = get_object_store(get_settings())
    db.add(note)
    db.flush()
    key = validate_key(f"notes/{user.id}/{note.id}/voice.{extension}")
    try:
        stored = store.put(key, bytes(body))
    except Exception:
        db.rollback()
        raise
    db.add(NoteAttachment(note_id=note.id, path=stored.key, media_type=media_type, size=stored.size, sha256=stored.sha256))
    db.add(NoteRevision(note_id=note.id, revision=1, body="", quoted_text=None, edited_by=audit.user_actor(user)))
    audit.record(db, actor=audit.user_actor(user), action="note.created", payload={"note_id": str(note.id), "kind": "voice"})
    db.commit()
    db.refresh(note)
    return NoteRead.model_validate(note)


@router.post("/photo", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def capture_photo(
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Capture an image note; the image is stored as an object and is never OCR'd."""
    existing = db.scalar(select(Note).where(Note.owner_id == user.id, Note.client_id == client_id))
    if existing is not None:
        return NoteRead.model_validate(existing)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    extensions = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    extension = extensions.get(media_type)
    if extension is None:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Photo must be JPEG, PNG, or WebP")
    max_bytes = get_settings().object_storage_max_bytes
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Photo is too large")
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Photo cannot be empty")
    note = Note(
        owner_id=user.id, project_id=None, client_id=client_id, kind=NoteKind.photo, body="",
        revision=1, captured_at=datetime.now(timezone.utc),
    )
    store = get_object_store(get_settings())
    db.add(note)
    db.flush()
    key = validate_key(f"notes/{user.id}/{note.id}/photo.{extension}")
    try:
        stored = store.put(key, bytes(body))
    except Exception:
        db.rollback()
        raise
    db.add(NoteAttachment(note_id=note.id, path=stored.key, media_type=media_type, size=stored.size, sha256=stored.sha256))
    db.add(NoteRevision(note_id=note.id, revision=1, body="", quoted_text=None, edited_by=audit.user_actor(user)))
    audit.record(db, actor=audit.user_actor(user), action="note.created", payload={"note_id": str(note.id), "kind": "photo"})
    db.commit()
    db.refresh(note)
    return NoteRead.model_validate(note)
# Declared before /{note_id} so "export" is not read as a note id (X.31.22).
@router.get("/export")
def export_notes(
    fmt: Literal["json", "md"] = Query(default="json", alias="format"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The caller's own notes in every status, newest first: a JSON list, or one Markdown document.
    Only the caller's own notes; a filed note's project readers do not get the export. Audited with format and count."""
    notes = list(db.scalars(select(Note).where(Note.owner_id == user.id).order_by(Note.captured_at.desc(), Note.created_at.desc(), Note.id.desc())))
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
    note = _load_own_note_for_change(db, note_id, user)
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
    note = _load_own_note_for_change(db, note_id, user)
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
    note = _load_own_note_for_change(db, note_id, user)
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
    refuse_if_archived(project)
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
    refuse_if_archived(project)
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
