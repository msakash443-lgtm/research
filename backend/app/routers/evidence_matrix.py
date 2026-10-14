"""Evidence table and exports (M3.5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.evidence_matrix import build, csv_bytes, xlsx_bytes
from app.contradictions import detect
from app.extraction_schema import SchemaError
from app.fulltext import FullTextError, extract_pdf_text
from app.models import Extraction, Project, Source
from app.object_storage import ObjectStorageError, get_object_store

router = APIRouter(prefix="/projects/{project_id}/evidence-matrix", tags=["evidence-matrix"])


def _matrix(db: Session, project: Project, schema_name: str) -> dict:
    try:
        return build(db, project, schema_name)
    except SchemaError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def matrix(
    schema_name: str = Query("default", max_length=80),
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> dict:
    return _matrix(db, project, schema_name)


@router.get("/contradictions")
def contradictions(
    schema_name: str = Query("default", max_length=80),
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> dict:
    matrix_data = _matrix(db, project, schema_name)
    return {"schema_version": matrix_data["schema_version"], "flags": detect(matrix_data)}


@router.get("/export.csv")
def export_csv(
    schema_name: str = Query("default", max_length=80),
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> Response:
    return Response(csv_bytes(_matrix(db, project, schema_name)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="evidence-matrix.csv"'})


@router.get("/export.xlsx")
def export_xlsx(
    schema_name: str = Query("default", max_length=80),
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> Response:
    return Response(xlsx_bytes(_matrix(db, project, schema_name)),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="evidence-matrix.xlsx"'})


@router.get("/{extraction_id}/fields/{field_key}/source-text")
def source_text(
    extraction_id: uuid.UUID, field_key: str,
    project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(select(Extraction).where(Extraction.id == extraction_id, Extraction.project_id == project.id))
    if row is None or (row.fields_json or {}).get(field_key) is None:
        raise HTTPException(404, "Evidence cell not found")
    span = (row.evidence_spans or {}).get(field_key)
    source = db.scalar(select(Source).where(Source.id == row.source_id, Source.project_id == project.id))
    if not isinstance(span, dict) or source is None or not source.fulltext_path:
        raise HTTPException(404, "Source text not found")
    try:
        settings = get_settings()
        fulltext = extract_pdf_text(get_object_store(settings).get(source.fulltext_path),
                                    max_pages=settings.fulltext_max_pages)
    except (ObjectStorageError, FullTextError) as exc:
        raise HTTPException(409, "Stored source text is unavailable") from exc
    matches = fulltext.find_quote(span["quote"], page=span.get("page"), section=span.get("section"))
    if not matches:
        raise HTTPException(409, "Evidence quote no longer matches stored source text")
    match = matches[0]
    start = max(0, match.start - 500)
    end = min(len(fulltext.text), match.end + 500)
    return {"source_id": str(source.id), "title": source.title, "page": list(match.pages),
            "section": match.section.title if match.section else None, "text": fulltext.text[start:end],
            "highlight_start": match.start - start, "highlight_end": match.end - start,
            "sha256": fulltext.sha256}
