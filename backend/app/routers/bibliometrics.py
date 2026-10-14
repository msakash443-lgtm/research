"""Project-scoped bibliometric inputs, views, and exports (M3.12)."""

from __future__ import annotations

import io
import uuid
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.bibliometric_networks import build, citespace_csv, vosviewer_files
from app.bibliometrics import publication_trends
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, Source, User

router = APIRouter(prefix="/projects/{project_id}/bibliometrics", tags=["bibliometrics"])


class BibliometricInput(BaseModel):
    reference_ids: list[str] = Field(max_length=500)
    keywords: list[str] = Field(max_length=100)

    @field_validator("reference_ids", "keywords")
    @classmethod
    def valid_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 300 or any(ord(char) < 32 for char in value)
               for value in values):
            raise ValueError("Items must be nonempty, at most 300 characters, and contain no control characters")
        return values


def _active_source(db: Session, project: Project, source_id: uuid.UUID) -> Source:
    source = db.scalar(select(Source).where(
        Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None)
    ))
    if source is None:
        raise HTTPException(404, "Source not found")
    return source


def _records(db: Session, project: Project) -> list[dict]:
    sources = db.scalars(select(Source).where(
        Source.project_id == project.id, Source.merged_into.is_(None)
    ).order_by(Source.id)).all()
    return [{"source_id": str(source.id), "title": source.title, "year": source.year,
             "authors": source.authors, "doi": source.doi,
             "reference_ids": source.bibliometric_reference_ids or [],
             "keywords": source.bibliometric_keywords or []} for source in sources]


@router.get("/inputs/{source_id}")
def get_inputs(source_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)),
               db: Session = Depends(get_db)) -> dict:
    source = _active_source(db, project, source_id)
    return {"source_id": str(source.id), "reference_ids": source.bibliometric_reference_ids,
            "keywords": source.bibliometric_keywords}


@router.put("/inputs/{source_id}")
def put_inputs(source_id: uuid.UUID, body: BibliometricInput,
               project: Project = Depends(project_access(WRITE_ROLES)),
               user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    source = _active_source(db, project, source_id)
    source.bibliometric_reference_ids = body.reference_ids
    source.bibliometric_keywords = body.keywords
    audit.record(db, actor=audit.user_actor(user), action="bibliometrics.inputs_saved",
                 project_id=project.id, payload={"source_id": str(source.id),
                                                "reference_count": len(body.reference_ids),
                                                "keyword_count": len(body.keywords)})
    project.touch()
    db.commit()
    return get_inputs(source_id, project, db)


@router.get("/networks")
def networks(project: Project = Depends(project_access(READ_ROLES)),
             db: Session = Depends(get_db)) -> dict:
    return build(_records(db, project))


@router.get("/export/vosviewer/{kind}.zip")
def export_vosviewer(kind: str, project: Project = Depends(project_access(READ_ROLES)),
                     db: Session = Depends(get_db)) -> Response:
    networks_data = build(_records(db, project))
    if kind not in {"co_citation", "bibliographic_coupling", "keyword_co_occurrence"}:
        raise HTTPException(404, "Network not found")
    map_text, network_text = vosviewer_files(networks_data[kind])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("map.txt", map_text)
        archive.writestr("network.txt", network_text)
    return Response(output.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-vosviewer.zip"'})


@router.get("/export/citespace.csv")
def export_citespace(project: Project = Depends(project_access(READ_ROLES)),
                     db: Session = Depends(get_db)) -> Response:
    return Response(citespace_csv(_records(db, project)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="citespace-converter-input.csv"'})


@router.get("/publication-trends")
def get_publication_trends(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Count publication years for active sources, without screening-based exclusions."""
    years = db.scalars(
        select(Source.year).where(Source.project_id == project.id, Source.merged_into.is_(None))
    ).all()
    return publication_trends(years)
