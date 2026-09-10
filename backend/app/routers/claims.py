from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import owned_project
from app.models import Claim, ClaimEvidence, Project, Source
from app.schemas import (
    ClaimCreate,
    ClaimEvidenceCreate,
    ClaimEvidenceRead,
    ClaimRead,
    ClaimUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/claims", tags=["claims"])


def _get_owned_claim(claim_id: uuid.UUID, project: Project, db: Session) -> Claim:
    claim = db.scalar(select(Claim).where(Claim.id == claim_id, Claim.project_id == project.id))
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    return claim


def _load_evidence(claim: Claim, db: Session) -> None:
    claim.evidence = db.scalars(
        select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id).order_by(ClaimEvidence.created_at.asc())
    ).all()


@router.get("", response_model=list[ClaimRead])
def list_claims(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    claims = db.scalars(
        select(Claim).where(Claim.project_id == project.id).order_by(Claim.created_at.desc())
    ).all()
    for claim in claims:
        _load_evidence(claim, db)
    return claims


@router.post("", response_model=ClaimRead, status_code=status.HTTP_201_CREATED)
def create_claim(
    payload: ClaimCreate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = Claim(project_id=project.id, text=payload.text, status=payload.status)
    db.add(claim)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(claim)
    _load_evidence(claim, db)
    return claim


@router.get("/{claim_id}", response_model=ClaimRead)
def get_claim(
    claim_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(claim_id, project, db)
    _load_evidence(claim, db)
    return claim


@router.patch("/{claim_id}", response_model=ClaimRead)
def update_claim(
    claim_id: uuid.UUID,
    payload: ClaimUpdate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(claim_id, project, db)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(claim, field, value)
    claim.updated_at = datetime.now(timezone.utc)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(claim)
    _load_evidence(claim, db)
    return claim


@router.delete("/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_claim(
    claim_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(claim_id, project, db)
    db.delete(claim)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()


@router.get("/{claim_id}/evidence", response_model=list[ClaimEvidenceRead])
def list_claim_evidence(
    claim_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(claim_id, project, db)
    _load_evidence(claim, db)
    return claim.evidence


@router.post("/{claim_id}/evidence", response_model=ClaimEvidenceRead, status_code=status.HTTP_201_CREATED)
def add_claim_evidence(
    claim_id: uuid.UUID,
    payload: ClaimEvidenceCreate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(claim_id, project, db)
    source = db.scalar(select(Source).where(Source.id == payload.source_id, Source.project_id == project.id))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found in this project")
    existing = db.scalar(
        select(ClaimEvidence).where(
            ClaimEvidence.claim_id == claim.id, ClaimEvidence.source_id == payload.source_id
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence from this source is already linked to the claim. Delete the existing link first.",
        )
    evidence = ClaimEvidence(
        claim_id=claim.id,
        source_id=payload.source_id,
        relationship=payload.relationship,
        excerpt=payload.excerpt,
        locator=payload.locator,
    )
    db.add(evidence)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(evidence)
    return evidence


@router.delete("/{claim_id}/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_claim_evidence(
    claim_id: uuid.UUID,
    evidence_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    claim = _get_owned_claim(claim_id, project, db)
    evidence = db.scalar(
        select(ClaimEvidence).where(ClaimEvidence.id == evidence_id, ClaimEvidence.claim_id == claim.id)
    )
    if evidence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found")
    db.delete(evidence)
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
