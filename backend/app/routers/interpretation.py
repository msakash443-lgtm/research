"""Findings-to-literature map and discussion-section draft (M6.9, spec 5.10 and 8, gate G9).

`app.interpretation_map` does the real work: generating the map from stored analysis runs and verified
extractions, persisting it as an append-only batch, and tracking whether a person has confirmed each
finding. Nothing here lets an AI confirm a finding, and G9 itself refuses to approve until every finding
in the current batch is confirmed (`routers/gates.py::_OWN_GATE_ROW`, EC-12.3).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from sqlalchemy.orm import Session

from app import audit, interpretation, interpretation_map
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import InterpretationFinding, Project, User
from app.suggestion_store import create_suggestion

router = APIRouter(prefix="/projects/{project_id}/interpretation", tags=["interpretation"])

DRAFT_MODEL_ID = "deterministic"
DRAFT_PROMPT_VERSION = "interpretation-draft-v1"
DRAFT_PRODUCING_AGENT = "interpretation-draft"
DRAFT_TARGET_KIND = "project"
DRAFT_TARGET_FIELD = "interpretation_discussion_draft"


class FindingRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    analysis_run_id: uuid.UUID
    output_key: str
    output_path: str
    value: float | None
    classification: str
    literature_source_ids: list[str]
    literature_extraction_ids: list[str]
    rationale: str
    generated_by: str
    created_at: datetime
    confirmed: bool


def _finding_read(db: Session, row: InterpretationFinding) -> FindingRead:
    return FindingRead(
        id=row.id,
        analysis_run_id=row.analysis_run_id,
        output_key=row.output_key,
        output_path=row.output_path,
        value=row.value,
        classification=row.classification,
        literature_source_ids=list(row.literature_source_ids or []),
        literature_extraction_ids=list(row.literature_extraction_ids or []),
        rationale=row.rationale,
        generated_by=row.generated_by,
        created_at=row.created_at,
        confirmed=interpretation_map.is_confirmed(db, row.id),
    )


@router.post("/generate", response_model=list[FindingRead], status_code=status.HTTP_201_CREATED)
def generate_findings(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[FindingRead]:
    """Build a fresh findings-to-literature map from every stored analysis run and this project's
    verified extractions, and persist it as a new append-only batch."""
    actor = audit.user_actor(user)
    try:
        rows = interpretation_map.generate_map(db, project, actor=actor)
    except interpretation_map.NoFindings as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record(
        db, actor=actor, action="interpretation.map_generated", project_id=project.id,
        payload={"batch_id": str(rows[0].batch_id), "count": len(rows)},
    )
    db.commit()
    return [_finding_read(db, row) for row in rows]


@router.get("/findings", response_model=list[FindingRead])
def list_findings(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[FindingRead]:
    """Every finding in the most recently generated batch, oldest first."""
    return [_finding_read(db, row) for row in interpretation_map.current_batch(db, project.id)]


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["confirmed", "disputed"]
    note: str | None = None


@router.post("/findings/{finding_id}/review", response_model=FindingRead)
def review_finding(
    finding_id: uuid.UUID,
    body: ReviewIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FindingRead:
    """A person confirms or disputes one computed finding. G9 cannot be approved until every finding in
    the current batch is confirmed this way (EC-12.3)."""
    row = interpretation_map.finding_in_project(db, project.id, finding_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
    actor = audit.user_actor(user)
    interpretation_map.record_review(db, project.id, row, decision=body.decision, reviewed_by=actor, note=body.note)
    audit.record(
        db, actor=actor, action="interpretation.finding_reviewed", project_id=project.id,
        payload={"finding_id": str(row.id), "decision": body.decision},
    )
    db.commit()
    return _finding_read(db, row)


class DraftRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_id: uuid.UUID
    content: dict[str, Any]


@router.post("/draft", response_model=DraftRead, status_code=status.HTTP_201_CREATED)
def draft_discussion(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DraftRead:
    """Suggest a discussion-section draft citing the current findings-to-literature map's agree/disagree
    items with `stat`/`cite` nodes only, never free prose numbers. Deterministically assembled from
    already-computed classifications (`model_id="deterministic"`): nothing here asks a model to judge
    agreement. The suggestion must be accepted, edited or discarded by a person like any other AI
    proposal (`app.section_draft`'s pattern); it is never written to a document automatically."""
    batch = interpretation_map.current_batch(db, project.id)
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no findings-to-literature map has been generated yet; generate one first",
        )
    items = [interpretation_map.to_item(row) for row in batch]
    content = interpretation.draft_content(items)
    actor = audit.user_actor(user)
    suggestion = create_suggestion(
        db,
        project_id=project.id,
        target_kind=DRAFT_TARGET_KIND,
        target_id=str(project.id),
        target_field=DRAFT_TARGET_FIELD,
        content=json.dumps(content),
        model_id=DRAFT_MODEL_ID,
        prompt_version=DRAFT_PROMPT_VERSION,
        producing_agent=DRAFT_PRODUCING_AGENT,
        action="interpretation_draft",
        input_chars=len(batch),
    )
    audit.record(
        db, actor=actor, action="ai.proposed", project_id=project.id,
        payload={"action": "interpretation_draft", "target_kind": DRAFT_TARGET_KIND, "target_id": str(project.id), "proposed": 1},
        model_id=DRAFT_MODEL_ID, prompt_version=DRAFT_PROMPT_VERSION,
    )
    db.commit()
    return DraftRead(suggestion_id=suggestion.id, content=content)
