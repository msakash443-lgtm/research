"""Evidence-linked claims proposed by a researcher or synthesis run (M3.11)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, APPROVE_ROLES, current_user, project_access
from app.models import Claim, ClaimEvidence, Extraction, Project, Source, SourceExcerpt, User, utcnow


router = APIRouter(prefix="/projects/{project_id}/claims", tags=["claims"])


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: uuid.UUID
    excerpt_id: uuid.UUID
    quote: str = Field(min_length=1, max_length=10000)


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=10000)
    extraction_id: uuid.UUID | None = None
    strength: str | None = Field(default=None, max_length=30)
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=25)


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source_id: uuid.UUID
    excerpt_id: uuid.UUID
    quote: str
    quote_matches_source: bool
    support_confirmed_by: str | None
    support_confirmed_at: datetime | None


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    extraction_id: uuid.UUID | None
    run_id: uuid.UUID | None
    text: str
    verified: bool
    strength: str | None
    section_id: uuid.UUID | None
    created_by: str
    created_at: datetime
    evidence: list[EvidenceRead]


def _claim_or_404(db: Session, project: Project, claim_id: uuid.UUID) -> Claim:
    claim = db.scalar(select(Claim).where(Claim.id == claim_id, Claim.project_id == project.id)
                      .options(selectinload(Claim.evidence)))
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    return claim


@router.get("", response_model=list[ClaimRead])
def list_claims(run_id: uuid.UUID | None = None,
                project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    query = select(Claim).where(Claim.project_id == project.id)
    if run_id is not None:
        query = query.where(Claim.run_id == run_id)
    return db.scalars(query
                      .order_by(Claim.created_at.desc()).options(selectinload(Claim.evidence))).all()


@router.post("", response_model=ClaimRead, status_code=status.HTTP_201_CREATED)
def create_claim(payload: ClaimIn, project: Project = Depends(project_access(WRITE_ROLES)),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.extraction_id and db.scalar(select(Extraction.id).where(
        Extraction.id == payload.extraction_id, Extraction.project_id == project.id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction not found")
    if len({item.source_id for item in payload.evidence}) != len(payload.evidence):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Use one evidence link per source")
    for item in payload.evidence:
        row = db.scalar(select(SourceExcerpt).join(Source).where(
            SourceExcerpt.id == item.excerpt_id, SourceExcerpt.source_id == item.source_id,
            Source.project_id == project.id, Source.merged_into.is_(None)))
        if row is None or item.quote not in row.content:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Evidence must quote an excerpt from an active project source")
    claim = Claim(project_id=project.id, extraction_id=payload.extraction_id,
                  text=payload.text.strip(), strength=payload.strength, verified=False,
                  created_by=str(user.id))
    claim.evidence = [ClaimEvidence(source_id=item.source_id, excerpt_id=item.excerpt_id,
                                    quote=item.quote) for item in payload.evidence]
    db.add(claim)
    db.flush()
    audit.record(db, actor=str(user.id), action="claim.created", project_id=project.id,
                 payload={"claim_id": str(claim.id), "evidence_count": len(claim.evidence)})
    db.commit()
    return _claim_or_404(db, project, claim.id)


@router.post("/{claim_id}/verify", response_model=ClaimRead)
def verify_claim(claim_id: uuid.UUID, project: Project = Depends(project_access(WRITE_ROLES | APPROVE_ROLES)),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    claim = _claim_or_404(db, project, claim_id)
    if claim.verified:
        return claim
    if not claim.evidence:
        raise HTTPException(status.HTTP_409_CONFLICT, "A claim needs evidence before verification")
    for link in claim.evidence:
        source = db.scalar(select(Source).where(Source.id == link.source_id,
                                               Source.project_id == project.id, Source.merged_into.is_(None)).with_for_update())
        excerpt = db.scalar(select(SourceExcerpt).where(SourceExcerpt.id == link.excerpt_id,
                                                        SourceExcerpt.source_id == link.source_id))
        if source is None or not source.metadata_verified or excerpt is None or link.quote not in excerpt.content:
            raise HTTPException(status.HTTP_409_CONFLICT, "Claim evidence is not a verified project source excerpt")
    claim.verified = True
    audit.record(db, actor=str(user.id), action="claim.verified", project_id=project.id,
                 payload={"claim_id": str(claim.id), "evidence_count": len(claim.evidence)})
    db.commit()
    return _claim_or_404(db, project, claim.id)


@router.post("/{claim_id}/evidence/{evidence_id}/confirm-support", response_model=ClaimRead)
def confirm_citation_support(claim_id: uuid.UUID, evidence_id: uuid.UUID,
                             project: Project = Depends(project_access(WRITE_ROLES | APPROVE_ROLES)),
                             user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A person judges whether a cited quotation actually supports the run sentence."""
    claim = _claim_or_404(db, project, claim_id)
    if claim.run_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only research-run citations use support confirmation")
    link = db.scalar(select(ClaimEvidence).where(ClaimEvidence.id == evidence_id,
                                                  ClaimEvidence.claim_id == claim.id).with_for_update())
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Citation evidence not found")
    if link.support_confirmed_by is not None:
        return claim
    source = db.scalar(select(Source).where(Source.id == link.source_id,
                                           Source.project_id == project.id, Source.merged_into.is_(None)).with_for_update())
    excerpt = db.scalar(select(SourceExcerpt).where(SourceExcerpt.id == link.excerpt_id,
                                                    SourceExcerpt.source_id == link.source_id))
    if source is None or not source.metadata_verified or excerpt is None or link.quote not in excerpt.content:
        raise HTTPException(status.HTTP_409_CONFLICT, "Citation source is no longer verified evidence")
    link.support_confirmed_by = audit.user_actor(user)
    link.support_confirmed_at = utcnow()
    audit.record(db, actor=link.support_confirmed_by, action="citation.support_confirmed",
                 project_id=project.id, payload={"run_id": str(claim.run_id), "claim_id": str(claim.id),
                                                 "evidence_id": str(link.id), "source_id": str(link.source_id)})
    db.commit()
    return _claim_or_404(db, project, claim.id)
