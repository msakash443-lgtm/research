from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.journal_matcher import JournalCandidate as MatchCandidate, rank
from app.models import JournalCandidate, Project, User
from app.schemas import JournalCandidateCreate, JournalCandidateRead, JournalMatchRequest

router = APIRouter(prefix="/projects/{project_id}/journals", tags=["journals"])


def _match_row(row: JournalCandidate) -> MatchCandidate:
    return MatchCandidate(
        name=row.name, scope_terms=tuple(row.scope_terms or []), indexes=tuple(row.indexes or []),
        apc=row.apc, review_days=row.review_days, source_url=row.source_url,
        checked_at=row.checked_at, official_predatory=row.official_predatory,
    )


@router.get("", response_model=list[JournalCandidateRead])
def list_journals(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(JournalCandidate).where(JournalCandidate.project_id == project.id).order_by(JournalCandidate.name)).all()


@router.post("", response_model=JournalCandidateRead, status_code=status.HTTP_201_CREATED)
def create_journal(
    body: JournalCandidateCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = JournalCandidate(project_id=project.id, created_by=audit.user_actor(user), **body.model_dump(mode="json"))
    row.source_url = str(body.source_url)
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="journal_candidate.created", project_id=project.id,
                 payload={"journal_candidate_id": str(row.id), "official_predatory": row.official_predatory})
    db.commit()
    db.refresh(row)
    return row


@router.post("/official-list", response_model=list[JournalCandidateRead], status_code=status.HTTP_201_CREATED)
def ingest_official_list(
    body: list[JournalCandidateCreate],
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    actor = audit.user_actor(user)
    rows = []
    for item in body:
        data = item.model_dump(mode="json")
        data["source_url"] = str(item.source_url)
        data["official_predatory"] = True
        row = JournalCandidate(project_id=project.id, created_by=actor, **data)
        db.add(row)
        rows.append(row)
    db.flush()
    audit.record(db, actor=actor, action="journal_candidate.official_list_ingested", project_id=project.id,
                 payload={"count": len(rows), "source_urls": sorted({row.source_url for row in rows})})
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


@router.post("/match")
def match_journals(
    body: JournalMatchRequest,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(JournalCandidate).where(JournalCandidate.project_id == project.id)).all()
    return rank([_match_row(row) for row in rows], scope=body.scope, required_index=body.required_index,
                max_apc=body.max_apc, max_review_days=body.max_review_days)
