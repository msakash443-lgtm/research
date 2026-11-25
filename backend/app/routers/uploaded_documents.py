"""Uploaded research documents (plan M8.1, M8.2): a person uploads a PDF or DOCX for the research-figures work.

The body is the raw file (no multipart), like `sources.py::upload_fulltext`. The media type must be
one `app/uploaded_documents.py` accepts, the bytes must start with that format's signature, the text
must extract (422 with a `code`: `no_extractable_text`, `encrypted`, `too_long` or `unreadable`), and the size is
capped by `object_storage_max_bytes`. Each file is stored once under a fresh id and never replaced.
Withdrawing sets `withdrawn_at`; the row and the stored bytes remain.

`GET .../content` re-extracts the stored bytes and returns them in the same shape as the source
full-text route. A withdrawn document has no content (409).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.fulltext import FullTextError
from app.models import Project, UploadedDocument, User, utcnow
from app.object_storage import get_object_store, uploaded_document_key
from app.routers.sources import FullTextPageRead, FullTextRead, FullTextSectionRead
from app.uploaded_documents import (
    MAX_FILENAME_LENGTH,
    NotThatFormat,
    UnsupportedMediaType,
    check_signature,
    error_code,
    extract_text,
    format_for,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["uploaded-documents"])


class UploadedDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    media_type: str
    sha256: str
    size: int
    uploaded_by: str
    created_at: datetime
    withdrawn_at: datetime | None


def _require_enabled() -> None:
    if not get_settings().research_figures_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research figures are not enabled")


def _load(db: Session, project_id: uuid.UUID, upload_id: uuid.UUID) -> UploadedDocument:
    row = db.scalar(select(UploadedDocument).where(UploadedDocument.id == upload_id, UploadedDocument.project_id == project_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uploaded document not found")
    return row


async def _read_capped(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File is too large")
        except ValueError:
            pass
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File is too large")
    return bytes(body)


def _extraction_error(exc: FullTextError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": error_code(exc), "message": str(exc)},
    )


@router.post("/uploaded-documents/upload", response_model=UploadedDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    filename: str = Query(..., max_length=MAX_FILENAME_LENGTH),
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Store a PDF or DOCX for this project. Refused: an unknown media type (415), a body over the size cap
    (413), bytes that don't start with that format's signature (422, detail is a string), or a file whose
    text does not extract (422, detail is `{code, message}`)."""
    _require_enabled()
    name = filename.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="filename must not be blank")
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        fmt = format_for(media_type)
    except UnsupportedMediaType as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload must be a PDF or DOCX") from exc
    settings = get_settings()
    data = await _read_capped(request, settings.object_storage_max_bytes)
    try:
        check_signature(fmt, data)
    except NotThatFormat as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"The uploaded file is not a {exc}") from exc
    try:
        await run_in_threadpool(
            extract_text, fmt, data,
            max_pdf_pages=settings.fulltext_max_pages, max_docx_paragraphs=settings.uploaded_docx_max_paragraphs,
        )
    except FullTextError as exc:
        raise _extraction_error(exc) from exc
    upload_id = uuid.uuid4()
    stored = get_object_store(settings).put(uploaded_document_key(project.id, upload_id, fmt.extension), data)
    actor = audit.user_actor(user)
    row = UploadedDocument(
        id=upload_id, project_id=project.id, filename=name, media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(), size=len(data), storage_key=stored.key, uploaded_by=actor,
    )
    db.add(row)
    audit.record(
        db, actor=actor, action="uploaded_document.created", project_id=project.id,
        payload={"upload_id": str(upload_id), "media_type": media_type, "sha256": row.sha256, "size": row.size},
    )
    db.commit()
    db.refresh(row)
    return row


@router.get("/uploaded-documents", response_model=list[UploadedDocumentRead])
def list_documents(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    _require_enabled()
    return db.scalars(
        select(UploadedDocument).where(UploadedDocument.project_id == project.id).order_by(UploadedDocument.created_at, UploadedDocument.id)
    ).all()


@router.get("/uploaded-documents/{upload_id}", response_model=UploadedDocumentRead)
def get_document(upload_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    _require_enabled()
    return _load(db, project.id, upload_id)


@router.get("/uploaded-documents/{upload_id}/content", response_model=FullTextRead)
def get_document_content(upload_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """The text of a stored document, with page/section anchors (empty pages and sections for a DOCX
    without headings). Re-extracted from the stored bytes on each call."""
    _require_enabled()
    row = _load(db, project.id, upload_id)
    if row.withdrawn_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This document is withdrawn")
    settings = get_settings()
    data = get_object_store(settings).get(row.storage_key)
    try:
        text = extract_text(
            format_for(row.media_type), data,
            max_pdf_pages=settings.fulltext_max_pages, max_docx_paragraphs=settings.uploaded_docx_max_paragraphs,
        )
    except FullTextError as exc:
        raise _extraction_error(exc) from exc
    return FullTextRead(
        text=text.text,
        pages=[FullTextPageRead(number=p.number, label=p.label, start=p.start, end=p.end) for p in text.pages],
        sections=[FullTextSectionRead(title=s.title, name=s.name, page=s.page, start=s.start) for s in text.sections],
        empty_pages=list(text.empty_pages),
        sha256=text.sha256,
    )


@router.post("/uploaded-documents/{upload_id}/withdraw", response_model=UploadedDocumentRead)
def withdraw_document(
    upload_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_enabled()
    row = _load(db, project.id, upload_id)
    if row.withdrawn_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This document is already withdrawn")
    row.withdrawn_at = utcnow()
    audit.record(
        db, actor=audit.user_actor(user), action="uploaded_document.withdrawn", project_id=project.id,
        payload={"upload_id": str(upload_id), "sha256": row.sha256, "size": row.size},
    )
    db.commit()
    db.refresh(row)
    return row
