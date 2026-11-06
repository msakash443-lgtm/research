"""Shared "row in this project or 404" lookups for routers (X.69.1, X.69.2)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, Project, Source


def project_document_or_404(db: Session, project: Project, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def live_source_or_404(db: Session, project: Project, source_id: uuid.UUID) -> Source:
    """A source of this project that has not been merged into another one."""
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None)))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source
