"""AI extraction fields exposed through the shared suggestion workflow (X.46.7)."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import suggestion_store
from app.artifacts import refresh_artifact
from app.models import AiSuggestion, Artifact, Extraction, Gate, GateCode, GateStatus, Project

TARGET_KIND = "extraction"


def _row(db: Session, suggestion: AiSuggestion) -> tuple[Project, Extraction]:
    project = db.get(Project, suggestion.project_id)
    row = db.get(Extraction, uuid.UUID(suggestion.target_id))
    if project is None or row is None or row.project_id != project.id:
        raise ValueError("The extraction this suggestion is about no longer exists.")
    return project, row


def _apply(db: Session, suggestion: AiSuggestion, text: str, actor: str) -> str:
    project, row = _row(db, suggestion)
    gate = db.scalar(
        select(Gate).where(Gate.project_id == project.id, Gate.code == GateCode.G4).with_for_update()
    )
    if gate is not None and gate.status == GateStatus.approved:
        raise ValueError("Gate G4 is approved; reopen the extraction stage before changing an extraction.")
    field = suggestion.target_field
    if not field:
        raise ValueError("This extraction suggestion has no field.")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = text
    fields = dict(row.fields_json or {})
    evidence = dict(row.evidence_spans or {})
    fields[field] = value
    if suggestion.evidence_json:
        evidence[field] = suggestion.evidence_json[0]
    row.fields_json = fields
    row.evidence_spans = evidence
    row.verified_by_human = False
    row.verified_by = None
    row.verified_at = None
    artifact = db.scalar(select(Artifact).where(
        Artifact.project_id == project.id, Artifact.kind == "extraction", Artifact.ref_id == str(row.id)
    ))
    if artifact is not None:
        refresh_artifact(db, artifact)
    return str(row.id)


def _revert(db: Session, suggestion: AiSuggestion, result_ref: str | None, actor: str) -> str | None:
    raise ValueError("Extraction suggestions cannot be undone because extraction rows have no field history; edit the field instead.")


suggestion_store.TARGET_HANDLERS[TARGET_KIND] = suggestion_store.TargetHandler(_apply, _revert)
