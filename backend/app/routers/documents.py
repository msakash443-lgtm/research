"""Manuscript sections: save, restore, version history and the authorship ledger (X.31.9).

Saving writes the next `DocumentVersion` and its `AuthorshipEvent` rows in one transaction, after the rules in
`app/ledger.py` pass. Nothing already written is changed (`ledger_guard` refuses it). A retry with the same
`client_save_id` returns the version it already wrote; a stale `base_number` is refused with `version_conflict`.
Only this router writes document versions, and only for a signed-in person.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, ledger, manuscript_guard, number_citation, overclaim_check, provenance_store, section_approval, section_draft_task, suggestions
from app.database import get_db
from app.front_matter import OUTPUT_KINDS
from app.lookups import project_document_or_404
from app.similarity_task import latest_version
from app.task_handlers import FRONT_MATTER, SECTION_DRAFT
from app.task_queue import enqueue_task
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access, project_role, refuse_if_archived
from app.gates import GateCode, required_roles
from app.models import (
    AiInvolvement,
    AiSuggestion,
    AnalysisRun,
    ArtifactStatus,
    AuthorshipEvent,
    AuthorshipKind,
    Claim,
    Document,
    DocumentVersion,
    ManuscriptOutput,
    Note,
    Project,
    SectionApproval,
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


class NumberOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_run_id: str
    path: str
    value: float


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


def _number_run_outputs(db: Session, project: Project) -> dict[str, Any]:
    """This project's analysis-run outputs, keyed by run id (M6.3): what a new `stat` node's path may resolve against."""
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project.id)).all()
    return {str(run.id): run.output_json for run in runs}



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
    number_runs = _number_run_outputs(db, project)
    content = ledger.pin_stat_values(content, number_runs)
    accepted, texts = _ai_context(db, project, document)
    problems = ledger.check_integrity(
        previous, content, actor=actor, accepted=accepted, suggestion_texts=texts,
        citable_source_ids=_citable_source_ids(db, project), number_run_outputs=number_runs, restored=restored,
        ai_wording_allowed=project.ai_involvement == AiInvolvement.full,
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
    section_approval.mark_stale(db, project.id, document.id)

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


@router.get("/numbers", response_model=list[NumberOption])
def list_insertable_numbers(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Every real number in this project's analysis-run outputs, flattened to the dot-paths a `stat` node can name
    (M6.3.1): what the Write step's number panel offers to insert. A static path, so it must stay registered before
    `GET /{document_id}` below or it would be swallowed as a (non-UUID) document id."""
    outputs = _number_run_outputs(db, project)
    return [
        {"analysis_run_id": run_id, "path": path, "value": value}
        for run_id, output in outputs.items()
        for path, value in number_citation.flatten_numbers(output)
    ]


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


class OutlineClaim(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    claim_id: uuid.UUID
    text: str
    verified: bool
    evidence_count: int


@router.get("/{document_id}/outline", response_model=list[OutlineClaim])
def section_outline(document_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)),
                    db: Session = Depends(get_db)):
    """The claims bound to this section, oldest first, with verification state. Claims in other sections or unbound are left out."""
    document = project_document_or_404(db, project, document_id)
    claims = db.scalars(select(Claim).where(Claim.project_id == project.id, Claim.section_id == document.id)
                        .order_by(Claim.created_at, Claim.id)).all()
    return [OutlineClaim(claim_id=claim.id, text=claim.text, verified=claim.verified,
                         evidence_count=len(claim.evidence)) for claim in claims]


class DraftSuggestionQueued(BaseModel):
    task_id: uuid.UUID
    status: str


@router.post("/{document_id}/draft-suggestion", status_code=status.HTTP_202_ACCEPTED, response_model=DraftSuggestionQueued)
def request_section_draft(
    document_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue an LLM draft of this section's opening prose from its bound claim outline (X.46.8 / M6.1.a). Refused
    before queueing if the project's AI is off or the section is marked 'never send to AI'. Per Q27 the result is
    always a suggestion under the shared `/suggestions` endpoints: nothing is written to the section unless a
    person accepts it and presses Insert."""
    try:
        section_draft_task.check_draft_request(db, project, document_id)
    except section_draft_task.Refused as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    actor = audit.user_actor(user)
    task = enqueue_task(
        db, project.id, SECTION_DRAFT,
        payload={"project_id": str(project.id), "document_id": str(document_id), "actor": actor},
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="ai.requested", project_id=project.id,
        payload={"task_id": str(task.id), "action": "draft", "target_kind": AI_TARGET_KIND, "target_id": str(document_id)},
    )
    db.commit()
    return DraftSuggestionQueued(task_id=task.id, status=task.status.value)

class FrontMatterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["abstract", "keywords", "highlights", "cover_letter"]


class FrontMatterQueued(BaseModel):
    task_id: uuid.UUID
    status: str
    kind: str
    source_version_id: uuid.UUID


@router.post("/{document_id}/front-matter", status_code=status.HTTP_202_ACCEPTED, response_model=FrontMatterQueued)
def request_front_matter(
    document_id: uuid.UUID,
    body: FrontMatterRequest,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue one front-matter proposal against the current saved document version."""
    if project.ai_involvement.value == "off":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "ai_off", "message": "AI is disabled for this project."})
    document = project_document_or_404(db, project, document_id)
    version = latest_version(db, document.id)
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "no_saved_version", "message": "Save the document before generating front matter."})
    actor = audit.user_actor(user)
    task = enqueue_task(
        db, project.id, FRONT_MATTER,
        payload={"project_id": str(project.id), "document_id": str(document.id), "kind": body.kind,
                 "actor": actor, "source_version_id": str(version.id)},
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="ai.requested", project_id=project.id,
        payload={"task_id": str(task.id), "action": "front_matter", "kind": body.kind,
                 "target_kind": "document", "target_id": str(document.id), "source_version_id": str(version.id)},
    )
    db.commit()
    return FrontMatterQueued(task_id=task.id, status=task.status.value, kind=body.kind, source_version_id=version.id)


@router.get("/{document_id}/front-matter")
def list_front_matter(
    document_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    document = project_document_or_404(db, project, document_id)
    return db.scalars(
        select(ManuscriptOutput).where(
            ManuscriptOutput.project_id == project.id, ManuscriptOutput.document_id == document.id,
        ).order_by(ManuscriptOutput.created_at, ManuscriptOutput.id)
    ).all()



class LinkFinding(BaseModel):
    kind: str
    subject: str
    message: str


class LinkCheckRead(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID | None
    ok: bool
    findings: list[LinkFinding]
    cited_source_ids: list[str]
    reference_source_ids: list[str]
    orphan_claim_ids: list[str]


def _source_id_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


@router.get("/{document_id}/link-check", response_model=LinkCheckRead)
def section_link_check(document_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)),
                       db: Session = Depends(get_db)):
    """Claims, citations and inserted numbers of this section checked against the project's sources and analysis runs
    (M6.2, M6.3): orphan claims, citations to sources that are unknown or unverified, verified evidence the text never
    cites, and inserted numbers that name an unknown analysis run or a path that does not resolve to a number. Reads
    the latest saved version and the claims bound to the section; it changes nothing and does not block export."""
    document = project_document_or_404(db, project, document_id)
    version = latest_version(db, document.id)
    content = version.content_json if version is not None else {"type": "doc", "content": []}
    report = manuscript_guard.section_report(db, project.id, document, content)
    return LinkCheckRead(
        document_id=document.id, version_id=version.id if version is not None else None, ok=report.ok,
        findings=[LinkFinding(kind=f.kind, subject=f.subject, message=f.message) for f in report.findings],
        cited_source_ids=report.cited_source_ids, reference_source_ids=report.reference_source_ids,
        orphan_claim_ids=report.orphan_claim_ids,
    )


class OverclaimFinding(BaseModel):
    kind: str
    subject: str
    message: str


class OverclaimCheckRead(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID | None
    design: str
    ok: bool
    findings: list[OverclaimFinding]


@router.get("/{document_id}/overclaim-check", response_model=OverclaimCheckRead)
def section_overclaim_check(document_id: uuid.UUID,
                            design: str = Query("observational", pattern="^(observational|experimental|unspecified)$"),
                            project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Causal wording in a non-experimental study and claims that reach past any sample, in the latest saved version of
    this section (M6.7, spec 5.10). Advisory: it reads one saved version, changes nothing and does not block export."""
    document = project_document_or_404(db, project, document_id)
    version = latest_version(db, document.id)
    content = version.content_json if version is not None else {"type": "doc", "content": []}
    report = overclaim_check.check_text(ledger.plain_text(content), design)
    return OverclaimCheckRead(
        document_id=document.id, version_id=version.id if version is not None else None, design=report.design,
        ok=report.ok, findings=[OverclaimFinding(kind=f.kind, subject=f.subject, message=f.message) for f in report.findings],
    )


class SectionApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


class SectionApprovalRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    report_id: uuid.UUID
    ai_share_pct: float
    ai_shares: dict[str, float]
    approved_by: str
    note: str | None
    created_at: datetime
    stale: bool
    stale_reason: str | None


def _approval_read(db: Session, row: SectionApproval) -> SectionApprovalRead:
    artifact = section_approval.artifact_for(db, row)
    stale = artifact is None or artifact.status == ArtifactStatus.stale
    return SectionApprovalRead(
        id=row.id, project_id=row.project_id, document_id=row.document_id, version_id=row.version_id,
        version_number=row.version_number, report_id=row.report_id, ai_share_pct=row.ai_share_pct,
        ai_shares=row.ai_shares, approved_by=row.approved_by, note=row.note, created_at=row.created_at,
        stale=stale, stale_reason=artifact.stale_reason if artifact is not None else None,
    )


@router.post("/{document_id}/approve", response_model=SectionApprovalRead, status_code=status.HTTP_201_CREATED)
def approve_section(
    document_id: uuid.UUID,
    payload: SectionApprovalCreate | None = None,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Approve this section's current saved text and freeze its AI-involvement level (G10, spec 5.11).

    Only an owner or supervisor (G10's required role) may approve. The section needs a current, non-stale
    section provenance report (`POST .../provenance-reports`, scope `section`) covering its current saved
    version first: the AI-involvement numbers are copied from that report, never computed here."""
    refuse_if_archived(project)
    document = project_document_or_404(db, project, document_id)
    role = project_role(db, project, user)
    if role not in required_roles(GateCode.G10):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approving a section needs an owner or supervisor role")
    if document.current_version_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Save this section before approving it")
    try:
        row = section_approval.approve(db, project, document, user=user, note=payload.note if payload else None)
    except section_approval.NoCurrentReport:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "no_current_report",
                "message": "Generate a provenance report for this section's current version before approving",
            },
        ) from None
    db.commit()
    return _approval_read(db, row)


@router.get("/{document_id}/approval", response_model=SectionApprovalRead | None)
def section_approval_status(
    document_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """This section's current approval (G10), or null if it has none or its approval has gone stale."""
    document = project_document_or_404(db, project, document_id)
    row = section_approval.current_approval(db, document)
    return _approval_read(db, row) if row is not None else None
