from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import owned_project
from app.models import Project, Source, SourceExcerpt
from app.schemas import ExcerptCreate, ExcerptRead, SourceCreate, SourceRead, SourceUpdate

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])

def apa_citation(source: Source) -> str:
    authors = source.authors or []
    if authors:
        author_text = ", ".join(str(author).strip() for author in authors if str(author).strip())
    else:
        author_text = source.title
    year = f"({source.year or 'n.d.'})"
    title = source.title.rstrip(".")
    if source.source_type == "article":
        citation = f"{author_text} {year}. {title}."
    else:
        citation = f"{author_text} {year}. {title}."
    link = source.url or (f"https://doi.org/{source.doi}" if source.doi else "")
    return f"{citation} {link}".strip()


def mla_citation(source: Source) -> str:
    authors = ", ".join(str(author).strip() for author in (source.authors or []) if str(author).strip()) or source.title
    link = source.url or (f"https://doi.org/{source.doi}" if source.doi else "")
    year = str(source.year) if source.year else "n.d."
    return f'{authors}. "{source.title.rstrip(".")}". {year}. {link}'.strip()


def _load_excerpts(source: Source, db: Session) -> None:
    source.excerpts = db.scalars(
        select(SourceExcerpt).where(SourceExcerpt.source_id == source.id).order_by(SourceExcerpt.created_at.asc())
    ).all()


def source_response(source: Source) -> SourceRead:
    excerpt = source.excerpts[-1] if source.excerpts else None
    return SourceRead(
        id=source.id,
        title=source.title,
        url=source.url,
        doi=source.doi,
        authors=source.authors,
        year=source.year,
        source_type=source.source_type,
        metadata_verified=source.metadata_verified,
        evidence_excerpt=excerpt.content if excerpt else None,
        excerpt_locator=excerpt.locator if excerpt else None,
        apa_citation=apa_citation(source),
        mla_citation=mla_citation(source),
        created_at=source.created_at,
    )


def _get_owned_source(source_id: uuid.UUID, project: Project, db: Session) -> Source:
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    _load_excerpts(source, db)
    return source


@router.get("", response_model=list[SourceRead])
def list_sources(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    sources = db.scalars(select(Source).where(Source.project_id == project.id).order_by(Source.created_at.desc())).all()
    for source in sources:
        _load_excerpts(source, db)
    return [source_response(source) for source in sources]


@router.get("/search", response_model=list[SourceRead])
def search_sources(
    q: str = Query(min_length=2, max_length=200),
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    """Search the project's saved article/source library by metadata and evidence."""
    terms = [term.lower() for term in q.split() if term.strip()]
    sources = db.scalars(select(Source).where(Source.project_id == project.id)).all()
    ranked: list[tuple[int, Source]] = []
    for source in sources:
        _load_excerpts(source, db)
        haystack = " ".join(
            [
                source.title,
                source.source_type,
                source.doi or "",
                source.url or "",
                " ".join(str(author) for author in (source.authors or [])),
                " ".join(excerpt.content for excerpt in source.excerpts),
            ]
        ).lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            ranked.append((score, source))
    ranked.sort(key=lambda item: (-item[0], item[1].created_at), reverse=False)
    return [source_response(source) for _, source in ranked[:50]]


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"evidence_excerpt", "locator"}, mode="json")
    source = Source(project_id=project.id, **data)
    db.add(source)
    db.flush()
    if payload.evidence_excerpt:
        content = payload.evidence_excerpt.strip()
        db.add(
            SourceExcerpt(
                source_id=source.id,
                content=content,
                locator=payload.locator.strip() if payload.locator else None,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(source)
    _load_excerpts(source, db)
    return source_response(source)


@router.get("/{source_id}", response_model=SourceRead)
def get_source(
    source_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    source = _get_owned_source(source_id, project, db)
    return source_response(source)


@router.patch("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    source = _get_owned_source(source_id, project, db)
    updates = payload.model_dump(exclude_unset=True, mode="json")
    for field, value in updates.items():
        setattr(source, field, value)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(source)
    _load_excerpts(source, db)
    return source_response(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    db.delete(source)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()


# ---------------------------------------------------------------------------
# Excerpts sub-resource
# ---------------------------------------------------------------------------

@router.get("/{source_id}/excerpts", response_model=list[ExcerptRead])
def list_excerpts(
    source_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    source = _get_owned_source(source_id, project, db)
    return source.excerpts


@router.post("/{source_id}/excerpts", response_model=ExcerptRead, status_code=status.HTTP_201_CREATED)
def add_excerpt(
    source_id: uuid.UUID,
    payload: ExcerptCreate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    content = payload.content.strip()
    excerpt = SourceExcerpt(
        source_id=source.id,
        content=content,
        locator=payload.locator,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    db.add(excerpt)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(excerpt)
    return excerpt
