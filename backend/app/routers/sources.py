from __future__ import annotations

import uuid

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.connectors.factory import enabled_lookup_names, unpaywall_unavailable_reason
from app.models import Project, Source, SourceExcerpt, Task, TaskStatus, User, excerpt_hash, utcnow
from app.task_handlers import FULLTEXT_FETCH, SOURCE_CHECK
from app.task_queue import enqueue_task
from app.refmanager import parse_bibtex, parse_ris, to_bibtex, to_ris
from app.schemas import SourceCreate, SourceMerge, SourceRead
from app.source_merge import SourceMergeError, merge_sources

_OPEN = {TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused}
router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


def source_response(source: Source) -> SourceRead:
    excerpt = source.excerpts[-1] if source.excerpts else None
    return SourceRead(
        id=source.id,
        title=source.title,
        doi=source.doi,
        url=source.url,
        authors=source.authors,
        year=source.year,
        source_type=source.source_type,
        metadata_verified=source.metadata_verified,
        verification_method=(source.verification_method or "human") if source.metadata_verified else None,
        verified_at=source.verified_at,
        verification=source.verification,
        origin=source.origin,
        is_automated=source.is_automated,
        created_by=source.created_by,
        venue=source.venue,
        abstract=source.abstract,
        oa_url=source.oa_url,
        source_ids=source.source_ids,
        fulltext_path=source.fulltext_path,
        fulltext_access=source.fulltext_access,
        quality_flags=source.quality_flags,
        evidence_excerpt=excerpt.content if excerpt else None,
        excerpt_locator=excerpt.locator if excerpt else None,
        created_at=source.created_at,
    )


def _load_source(db: Session, source_id: uuid.UUID) -> Source:
    """Re-read a source and its excerpts from the database after a commit."""
    return db.scalars(
        select(Source).where(Source.id == source_id).options(selectinload(Source.excerpts)).execution_options(populate_existing=True)
    ).one()


@router.get("", response_model=list[SourceRead])
def list_sources(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    sources = db.scalars(
        select(Source)
        .where(Source.project_id == project.id, Source.merged_into.is_(None))
        .options(selectinload(Source.excerpts))
        .order_by(Source.created_at.desc())
    ).all()
    return [source_response(source) for source in sources]


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SourceCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"evidence_excerpt", "locator"}, mode="json")
    source = Source(project_id=project.id, created_by=audit.user_actor(user), **data)
    db.add(source)
    db.flush()
    if payload.evidence_excerpt:
        content = payload.evidence_excerpt.strip()
        db.add(
            SourceExcerpt(
                source_id=source.id,
                content=content,
                locator=payload.locator.strip() if payload.locator else None,
                content_hash=excerpt_hash(content),
                created_by=audit.user_actor(user),
            )
        )
    audit.record(
        db, actor=audit.user_actor(user), action="source.created", project_id=project.id,
        payload={"source_id": str(source.id), "has_excerpt": bool(payload.evidence_excerpt)},
    )
    project.touch()
    db.commit()
    return source_response(_load_source(db, source.id))


@router.post("/{source_id}/verify", response_model=SourceRead)
def mark_source_verified(
    source_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """A signed-in person confirms the source's metadata. Never callable by the agent or worker.

    The acting user is recorded as a `source.verified` audit event.
    """
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None)))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if not source.reviewed_by_person:
        upgraded = bool(source.metadata_verified)  # an automatic check a person now confirms
        source.metadata_verified = True
        source.verification_method = "human"
        source.verified_at = utcnow()
        source.verified_by = audit.user_actor(user)
        audit.record(
            db, actor=audit.user_actor(user), action="source.verified", project_id=project.id,
            payload={"source_id": str(source.id), "method": "human", "confirmed_automatic_check": upgraded},
        )
        project.touch()
        db.commit()
    return source_response(_load_source(db, source.id))


@router.post("/merge", response_model=SourceRead)
def merge_duplicate_sources(
    body: SourceMerge,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """A person merges sources that are the same work into one; the others are hidden, not deleted."""
    try:
        kept = merge_sources(db, project=project, source_ids=body.source_ids, actor=audit.user_actor(user), keep=body.keep)
    except SourceMergeError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return source_response(_load_source(db, kept.id))


class SourceCheckQueued(BaseModel):
    task_id: uuid.UUID
    status: str


@router.post("/{source_id}/check", response_model=SourceCheckQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_source_check(
    source_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue an automatic metadata check (title, authors, year, DOI against scholarly services).

    The result appears on the source (`verification`, and `metadata_verified` only if it matched).
    """
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None)))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if not enabled_lookup_names():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No lookup connector is enabled on this server")
    for task in db.scalars(select(Task).where(Task.project_id == project.id, Task.type == SOURCE_CHECK)):
        if task.status in _OPEN and (task.payload or {}).get("source_id") == str(source_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A check for this source is already waiting or running")
    actor = audit.user_actor(user)
    task = enqueue_task(db, project.id, SOURCE_CHECK, payload={"project_id": str(project.id), "source_id": str(source_id), "requested_by": actor}, actor=actor)
    audit.record(db, actor=actor, action="source.check_requested", project_id=project.id, payload={"source_id": str(source_id), "task_id": str(task.id)})
    db.commit()
    return SourceCheckQueued(task_id=task.id, status=task.status.value)


@router.post("/{source_id}/fulltext/fetch", response_model=SourceCheckQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_fulltext_fetch(
    source_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue an open-access full-text fetch (Unpaywall) for a source with a DOI.

    The PDF is stored only where its licence permits; otherwise the link and licence are kept.
    The outcome appears on the source as `fulltext_access` (and `fulltext_path` when stored).
    """
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None)))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if not source.doi:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This source has no DOI to look up")
    reason = unpaywall_unavailable_reason()
    if reason:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    if source.fulltext_path:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source already has stored full text")
    for task in db.scalars(select(Task).where(Task.project_id == project.id, Task.type == FULLTEXT_FETCH)):
        if task.status in _OPEN and (task.payload or {}).get("source_id") == str(source_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A full-text fetch for this source is already waiting or running")
    actor = audit.user_actor(user)
    task = enqueue_task(db, project.id, FULLTEXT_FETCH, payload={"project_id": str(project.id), "source_id": str(source_id), "requested_by": actor}, actor=actor)
    audit.record(db, actor=actor, action="source.fulltext_requested", project_id=project.id, payload={"source_id": str(source_id), "task_id": str(task.id)})
    db.commit()
    return SourceCheckQueued(task_id=task.id, status=task.status.value)


class ReferenceImport(BaseModel):
    format: Literal["bibtex", "ris"]
    text: str = Field(min_length=1, max_length=2_000_000)


class ReferenceImportResult(BaseModel):
    created: int
    skipped: list[str]


@router.post("/import", response_model=ReferenceImportResult, status_code=status.HTTP_201_CREATED)
def import_references(
    body: ReferenceImport,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Add sources from a BibTeX or RIS file. They arrive unverified; a bad or duplicate entry is
    reported in `skipped` and the rest still import."""
    parsed = (parse_bibtex if body.format == "bibtex" else parse_ris)(body.text)
    skipped = list(parsed.skipped)
    known_dois = set(db.scalars(select(Source.doi).where(Source.project_id == project.id, Source.doi.is_not(None), Source.merged_into.is_(None))))
    actor = audit.user_actor(user)
    created = 0
    for record in parsed.records:
        try:
            item = SourceCreate(**record)
        except (ValidationError, ValueError) as exc:
            skipped.append(f"{str(record.get('title'))[:80]}: {exc.errors()[0]['loc'][0] if isinstance(exc, ValidationError) else exc}")
            continue
        if item.doi and item.doi in known_dois:
            skipped.append(f"{item.title[:80]}: DOI already in this project")
            continue
        if item.doi:
            known_dois.add(item.doi)
        db.add(Source(project_id=project.id, created_by=actor, **item.model_dump(mode="json", exclude={"evidence_excerpt", "locator"})))
        created += 1
    audit.record(
        db, actor=actor, action="sources.imported", project_id=project.id,
        payload={"format": body.format, "created": created, "skipped": len(skipped)},
    )
    if created:
        project.touch()
    db.commit()
    return ReferenceImportResult(created=created, skipped=skipped)


@router.get("/export")
def export_references(
    format: Literal["bibtex", "ris"] = "bibtex",
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Verified sources as BibTeX or RIS. Unverified ones are left out (plan rule 24); how many is in
    the `X-Unverified-Skipped` header."""
    sources = db.scalars(select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None)).order_by(Source.created_at)).all()
    verified = [s for s in sources if s.metadata_verified]
    records = [
        {k: v for k, v in {"title": s.title, "authors": s.authors, "year": s.year, "doi": s.doi, "url": s.url,
                           "venue": s.venue, "abstract": s.abstract, "source_type": s.source_type}.items() if v}
        for s in verified
    ]
    audit.record(
        db, actor=audit.user_actor(user), action="sources.exported", project_id=project.id,
        payload={"format": format, "exported": len(verified), "unverified_skipped": len(sources) - len(verified)},
    )
    db.commit()
    text = to_bibtex(records) if format == "bibtex" else to_ris(records)
    ext = "bib" if format == "bibtex" else "ris"
    return Response(
        content=text, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sources.{ext}"', "X-Unverified-Skipped": str(len(sources) - len(verified))},
    )
