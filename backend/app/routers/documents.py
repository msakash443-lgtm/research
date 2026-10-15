"""Manuscript sections: save, restore, version history and the authorship ledger (X.31.9).

Saving writes the next `DocumentVersion` and its `AuthorshipEvent` rows in one transaction, after the rules in
`app/ledger.py` pass. Nothing already written is changed (`ledger_guard` refuses it). A retry with the same
`client_save_id` returns the version it already wrote; a stale `base_number` is refused with `version_conflict`.
Only this router writes document versions, and only for a signed-in person.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, ledger, provenance_store, suggestions
from app.database import get_db
from app.lookups import project_document_or_404
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import (
    AiSuggestion,
    AuthorshipEvent,
    AuthorshipKind,
    Document,
    DocumentVersion,
    Note,
    Project,
    Source,
    SourceExcerpt,
    SpanOrigin,
    User,
)

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])

AI_TARGET_KIND = "document"  # AI suggestions on a section use this target kind (X.31.11 builds the producer)


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    position: int = Field(ge=0)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a section needs a title")
        return value


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=300)
    position: int | None = Field(default=None, ge=0)
    ai_locked: bool | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("a section needs a title")
        return value

    @model_validator(mode="after")
    def _some_field_and_no_nulls(self):
        sent = self.model_dump(exclude_unset=True)
        if not sent:
            raise ValueError("send at least one field to change")
        if any(value is None for value in sent.values()):
            raise ValueError("these fields cannot be null")
        return self


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    position: int
    ai_locked: bool
    current_version_id: uuid.UUID | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class VersionSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_number: int = Field(ge=0)
    client_save_id: str = Field(min_length=1, max_length=64)
    client_session_id: str | None = Field(default=None, max_length=64)
    client_saved_at: datetime | None = None
    content_json: dict[str, Any]


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_number: int = Field(ge=0)


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    document_id: uuid.UUID
    number: int
    parent_number: int | None
    content_json: dict[str, Any]
    content_text: str
    content_hash: str
    saved_by: str
    client_save_id: str
    client_session_id: str | None
    client_saved_at: datetime | None
    restored_from_number: int | None
    created_at: datetime


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    number: int
    parent_number: int | None
    content_hash: str
    saved_by: str
    restored_from_number: int | None
    created_at: datetime


class DetectedSource(BaseModel):
    span_id: str
    source_id: str | None
    document_id: str | None


class SaveResult(BaseModel):
    version: VersionRead
    detected: list[DetectedSource]


class FromNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: uuid.UUID


class FromNoteFragment(BaseModel):
    note_id: uuid.UUID
    span_id: str
    content: list[dict[str, Any]]


class LedgerEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_id: uuid.UUID
    span_id: str
    kind: AuthorshipKind
    origin: SpanOrigin
    paste_status: str | None
    actor: str
    chars_before: int
    chars_after: int
    text_hash: str
    suggestion_id: uuid.UUID | None
    note_id: uuid.UUID | None
    detected_source_id: uuid.UUID | None
    detected_document_id: uuid.UUID | None
    created_at: datetime


def _latest(db: Session, document: Document) -> DocumentVersion | None:
    return db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.number.desc()).limit(1))


def _number(latest: DocumentVersion | None) -> int:
    return latest.number if latest is not None else 0


def _conflict(latest: DocumentVersion | None) -> HTTPException:
    current = VersionRead.model_validate(latest).model_dump(mode="json") if latest is not None else None
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "version_conflict", "current": current})


def _refuse(problems: list[ledger.Problem]) -> None:
    first = problems[0]
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": first.code, "message": first.message, "codes": sorted({p.code for p in problems})},
    )


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def _ai_context(db: Session, project: Project, document: Document) -> tuple[dict[str, str], list[str]]:
    """Accepted suggestion ids → the text a person accepted, and every AI output for this section."""
    rows = db.scalars(select(AiSuggestion).where(
        AiSuggestion.project_id == project.id, AiSuggestion.target_kind == AI_TARGET_KIND, AiSuggestion.target_id == str(document.id)
    )).all()
    accepted: dict[str, str] = {}
    texts: list[str] = []
    for suggestion in rows:
        texts.append(suggestion.content)
        text = suggestions.accepted_text(suggestion.content, [suggestions.Event(e.kind, e.final_text) for e in suggestion.events])
        if text is not None:
            accepted[str(suggestion.id)] = text
    return accepted, texts


def _citable_source_ids(db: Session, project: Project) -> set[str]:
    sources = db.scalars(select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None))).all()
    return {str(source.id) for source in sources if source.reviewed_by_person}


def _paste_candidates(db: Session, project: Project, document: Document) -> list[tuple[str, str, str]]:
    """Text a paste can match, in the order detection tries it: excerpts and abstracts of this project's sources,
    then the latest saved text of the project's other sections."""
    candidates: list[tuple[str, str, str]] = []
    for source_id, content in db.execute(
        select(SourceExcerpt.source_id, SourceExcerpt.content).join(Source, Source.id == SourceExcerpt.source_id).where(Source.project_id == project.id)
    ):
        candidates.append(("source", str(source_id), content))
    for source_id, abstract in db.execute(select(Source.id, Source.abstract).where(Source.project_id == project.id, Source.abstract.is_not(None))):
        candidates.append(("source", str(source_id), abstract))
    for other_id, text in db.execute(
        select(Document.id, DocumentVersion.content_text)
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(Document.project_id == project.id, Document.id != document.id)
    ):
        candidates.append(("document", str(other_id), text))
    return candidates


def _store(
    db: Session,
    *,
    project: Project,
    document: Document,
    user: User,
    content: dict[str, Any],
    latest: DocumentVersion | None,
    client_save_id: str,
    client_session_id: str | None = None,
    client_saved_at: datetime | None = None,
    restored_from: DocumentVersion | None = None,
) -> tuple[DocumentVersion, list[DetectedSource]]:
    """Check, then add the next version and its events. Nothing is committed here; the caller commits."""
    actor = audit.user_actor(user)
    previous = latest.content_json if latest is not None else ledger.EMPTY_DOCUMENT
    restored = restored_from.content_json if restored_from is not None else None

    problems = ledger.validate_schema(content)
    if problems:
        _refuse(problems)
    accepted, texts = _ai_context(db, project, document)
    problems = ledger.check_integrity(
        previous, content, actor=actor, accepted=accepted, suggestion_texts=texts,
        citable_source_ids=_citable_source_ids(db, project), restored=restored,
    )
    if problems:
        _refuse(problems)

    number = _number(latest) + 1
    version = DocumentVersion(
        document_id=document.id, project_id=project.id, number=number,
        parent_number=latest.number if latest is not None else None, content_json=content,
        content_text=ledger.plain_text(content), content_hash=ledger.content_hash(content), saved_by=actor,
        client_save_id=client_save_id, client_session_id=client_session_id, client_saved_at=client_saved_at,
        restored_from_number=restored_from.number if restored_from is not None else None,
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _conflict(_latest(db, document)) from None

    matches = ledger.detect_pasted(previous, content, _paste_candidates(db, project, document))
    counts: dict[str, int] = {}
    for change in ledger.diff_spans(previous, content, restored=restored):
        counts[change.kind] = counts.get(change.kind, 0) + 1
        match = matches.get(change.span_id)
        db.add(AuthorshipEvent(
            project_id=project.id, document_id=document.id, version_id=version.id, span_id=change.span_id,
            kind=AuthorshipKind(change.kind), origin=SpanOrigin(change.origin), paste_status=change.paste_status,
            actor=actor, chars_before=change.chars_before, chars_after=change.chars_after, text_hash=change.text_hash,
            suggestion_id=_uuid_or_none(change.suggestion_id), note_id=_uuid_or_none(change.note_id),
            detected_source_id=uuid.UUID(match[1]) if match and match[0] == "source" else None,
            detected_document_id=uuid.UUID(match[1]) if match and match[0] == "document" else None,
        ))
    document.current_version_id = version.id
    provenance_store.mark_covering_reports_stale(db, project.id, document.id)

    detected = [
        DetectedSource(span_id=sid, source_id=cid if kind == "source" else None, document_id=cid if kind == "document" else None)
        for sid, (kind, cid) in matches.items()
    ]
    action = "document.restored" if restored_from is not None else "document.saved"
    audit.record(db, actor=actor, action=action, project_id=project.id,
                 payload={"document_id": str(document.id), "version": number, **counts, "detected": len(detected)})
    return version, detected


def _commit_or_conflict(db: Session, document: Document) -> None:
    try:
        db.commit()
    except IntegrityError:
        # Another save took this number between our check and our insert: nothing was written, so say so.
        db.rollback()
        raise _conflict(_latest(db, document)) from None


@router.get("", response_model=list[DocumentRead])
def list_documents(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """This project's sections in manuscript order. Text is not included: read a section's latest version for that."""
    return db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.position, Document.created_at)).all()


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    actor = audit.user_actor(user)
    document = Document(project_id=project.id, title=payload.title, position=payload.position, created_by=actor)
    db.add(document)
    db.flush()
    audit.record(db, actor=actor, action="document.created", project_id=project.id,
                 payload={"document_id": str(document.id), "position": payload.position})
    db.commit()
    db.refresh(document)
    return document


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: uuid.UUID,
    payload: DocumentPatch,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Change a section's title, position or AI lock. The text is untouched: only saves change it."""
    document = project_document_or_404(db, project, document_id)
    changes = payload.model_dump(exclude_unset=True)
    for field_name, value in changes.items():
        setattr(document, field_name, value)
    actor = audit.user_actor(user)
    audit.record(db, actor=actor, action="document.updated", project_id=project.id,
                 payload={"document_id": str(document.id), "fields": sorted(changes)})
    db.commit()
    db.refresh(document)
    return document


@router.get("/{document_id}", response_model=VersionRead)
def current_version(document_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    document = project_document_or_404(db, project, document_id)
    latest = _latest(db, document)
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This section has no saved versions yet")
    return latest


@router.post("/{document_id}/versions", response_model=SaveResult, status_code=status.HTTP_201_CREATED)
def save_version(
    document_id: uuid.UUID,
    payload: VersionSave,
    response: Response,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    document = project_document_or_404(db, project, document_id)
    replayed = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == document.id, DocumentVersion.client_save_id == payload.client_save_id
    ))
    if replayed is not None:
        response.status_code = status.HTTP_200_OK
        return SaveResult(version=VersionRead.model_validate(replayed), detected=[])

    latest = _latest(db, document)
    if payload.base_number != _number(latest):
        raise _conflict(latest)
    version, detected = _store(
        db, project=project, document=document, user=user, content=payload.content_json, latest=latest,
        client_save_id=payload.client_save_id, client_session_id=payload.client_session_id,
        client_saved_at=payload.client_saved_at,
    )
    _commit_or_conflict(db, document)
    return SaveResult(version=VersionRead.model_validate(version), detected=detected)


@router.get("/{document_id}/versions", response_model=list[VersionSummary])
def list_versions(document_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    document = project_document_or_404(db, project, document_id)
    return db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.number)).all()


@router.get("/{document_id}/versions/{number}", response_model=VersionRead)
def read_version(document_id: uuid.UUID, number: int, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    document = project_document_or_404(db, project, document_id)
    version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.number == number))
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found")
    return version


@router.post("/{document_id}/versions/{number}/restore", response_model=SaveResult)
def restore_version(
    document_id: uuid.UUID,
    number: int,
    payload: RestoreIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Copy an older version into a new latest version. Nothing is deleted; the copy's spans are `restored`."""
    document = project_document_or_404(db, project, document_id)
    target = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.number == number))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Version not found")
    latest = _latest(db, document)
    if payload.base_number != _number(latest):
        raise _conflict(latest)
    version, detected = _store(
        db, project=project, document=document, user=user, content=target.content_json, latest=latest,
        client_save_id=f"restore-{number}-{uuid.uuid4().hex}", restored_from=target,
    )
    _commit_or_conflict(db, document)
    return SaveResult(version=VersionRead.model_validate(version), detected=detected)


@router.post("/{document_id}/paragraph-from-note", response_model=FromNoteFragment)
def paragraph_from_note(
    document_id: uuid.UUID,
    payload: FromNoteIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Paragraphs for the editor to insert, one per line of the note's own words, all in one `from_note` span.
    Only the caller's own note filed to this project; anyone else's, or one elsewhere, looks absent. The quoted
    text of a clip is someone else's words and is never included. Nothing is saved: the next save records it."""
    project_document_or_404(db, project, document_id)
    note = db.get(Note, payload.note_id)
    if note is None or note.owner_id != user.id or note.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Note not found")
    lines = [line.strip() for line in note.body.splitlines() if line.strip()]
    if not lines:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "note_has_no_text", "message": "This note has none of your own words to turn into a paragraph"},
        )
    span_id = str(uuid.uuid4())
    prov = {"sid": span_id, "origin": SpanOrigin.from_note.value, "by": audit.user_actor(user),
            "sug": None, "note": str(note.id), "paste": None, "src": None}
    content = [{"type": "paragraph", "content": [{"type": "text", "text": line, "marks": [{"type": "prov", "attrs": prov}]}]}
               for line in lines]
    return FromNoteFragment(note_id=note.id, span_id=span_id, content=content)


@router.get("/{document_id}/ledger", response_model=list[LedgerEventRead])
def span_ledger(
    document_id: uuid.UUID,
    span_id: str = Query(min_length=1, max_length=36),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Every event for one span, oldest version first. Events hold no text: the text lives in the versions."""
    document = project_document_or_404(db, project, document_id)
    return db.scalars(
        select(AuthorshipEvent)
        .join(DocumentVersion, DocumentVersion.id == AuthorshipEvent.version_id)
        .where(AuthorshipEvent.document_id == document.id, AuthorshipEvent.span_id == span_id)
        .order_by(DocumentVersion.number, AuthorshipEvent.created_at)
    ).all()
