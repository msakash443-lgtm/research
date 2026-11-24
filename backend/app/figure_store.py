"""Reads and writes for figures and their versions (plan M8.3).

`write_version` is the only code that inserts a `FigureVersion`, so every version is numbered, validated against
its figure kind, and hashed the same way whether a person edits it or a suggestion is accepted. The figure row is
locked while the next number is taken, so two writers cannot both claim version N+1.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.fulltext import FullText
from app.object_storage import get_object_store
from app.uploaded_documents import extract_text, format_for
from app import figure_spec
from app.models import AiSuggestion, Figure, FigureVersion, UploadedDocument

ORIGIN_AI_ACCEPTED = "ai_accepted"
ORIGIN_HUMAN_EDITED = "human_edited"
ORIGINS = (ORIGIN_AI_ACCEPTED, ORIGIN_HUMAN_EDITED)


def get_figure(db: Session, project_id: uuid.UUID, figure_id: uuid.UUID) -> Figure | None:
    return db.scalar(select(Figure).where(Figure.id == figure_id, Figure.project_id == project_id))


def list_figures(db: Session, project_id: uuid.UUID) -> list[Figure]:
    return list(db.scalars(select(Figure).where(Figure.project_id == project_id).order_by(Figure.created_at, Figure.id)))


def list_versions(db: Session, figure: Figure) -> list[FigureVersion]:
    return list(db.scalars(select(FigureVersion).where(FigureVersion.figure_id == figure.id).order_by(FigureVersion.number)))


def version_by_number(db: Session, figure: Figure, number: int) -> FigureVersion | None:
    return db.scalar(select(FigureVersion).where(FigureVersion.figure_id == figure.id, FigureVersion.number == number))


def latest_version(db: Session, figure: Figure) -> FigureVersion | None:
    return db.scalar(
        select(FigureVersion).where(FigureVersion.figure_id == figure.id).order_by(FigureVersion.number.desc()).limit(1)
    )


def write_version(
    db: Session,
    figure: Figure,
    spec: Any,
    *,
    origin: str,
    created_by: str,
    suggestion: AiSuggestion | None = None,
) -> FigureVersion:
    """Append version N+1 of `figure` holding `spec`. Raises `figure_spec.SpecError` if the spec is not valid for the
    figure's kind; nothing is written in that case. The caller commits and audits."""
    if origin not in ORIGINS:
        raise ValueError(f"unknown figure version origin {origin!r}")
    db.scalar(select(Figure.id).where(Figure.id == figure.id).with_for_update())
    canonical = figure_spec.normalize(spec, figure.kind)
    current = db.scalar(select(func.max(FigureVersion.number)).where(FigureVersion.figure_id == figure.id)) or 0
    version = FigureVersion(
        project_id=figure.project_id,
        figure_id=figure.id,
        number=current + 1,
        spec_json=canonical,
        spec_hash=figure_spec.spec_hash(canonical, figure.kind),
        origin=origin,
        suggestion_id=suggestion.id if suggestion is not None else None,
        model_id=suggestion.model_id if suggestion is not None else None,
        prompt_version=suggestion.prompt_version if suggestion is not None else None,
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    return version


def document_full_text(settings: Settings, document: UploadedDocument) -> FullText:
    """The stored upload's full text, extracted the way figure generation extracts it. Raises `FullTextError` when
    the file cannot be read; the caller decides the failure code."""
    data = get_object_store(settings).get(document.storage_key)
    return extract_text(
        format_for(document.media_type),
        data,
        max_pdf_pages=settings.fulltext_max_pages,
        max_docx_paragraphs=settings.uploaded_docx_max_paragraphs,
    )
