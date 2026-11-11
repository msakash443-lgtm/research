"""Excerpt-level qualitative coding with model suggestions (M5.5.2, slice 2).

Participant text reaches a model only through `OpenAICompatibleLLM.for_participant_data`: the local endpoint, never the
third-party route, whatever the project's model route says. A suggestion is a proposal and stays pending until a person
accepts or rejects it. Audit entries record ids and counts, never the excerpt text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, qual_codebook_store, qual_suggest
from app.agent.llm import LLMConfigurationError, LLMResponseError, OpenAICompatibleLLM
from app.ai_controls import require_ai_on
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.llm_usage import LLMBudgetExceeded, ProjectMeter
from app.models import Project, QualExcerpt, QualSuggestion, User, utcnow

router = APIRouter(prefix="/projects/{project_id}/qual", tags=["qual-coding"])

MAX_EXCERPT_CHARS = 5000


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_id: uuid.UUID
    decision: Literal["accept", "reject"]
    # The person's definition for the code. On accept of a new code, required when the suggestion has no rationale.
    definition: str | None = Field(default=None, max_length=1000)


class RenameCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)


class MergeCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _suggestion_read(row: QualSuggestion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "is_new": row.is_new,
        "rationale": row.rationale,
        "model_id": row.model_id,
        "prompt_version": row.prompt_version,
        "status": row.status,
        "decided_by": row.decided_by,
        "decided_at": _iso(row.decided_at),
    }


def _excerpt_read(excerpt: QualExcerpt, suggestions: list[QualSuggestion]) -> dict[str, Any]:
    return {
        "id": str(excerpt.id),
        "text": excerpt.text,
        "suggestions": [_suggestion_read(s) for s in suggestions],
    }


def _accepted_codes(db: Session, project: Project) -> dict[str, str]:
    """Codes a person has already accepted into the project's codebook, with their definitions."""
    return qual_codebook_store.active_codes(db, project.id)


@router.post("/suggest", status_code=status.HTTP_201_CREATED)
def suggest(
    body: SuggestIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Store one excerpt and ask the local model for pending code suggestions."""
    require_ai_on(project)
    if not body.excerpt.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The excerpt is blank; nothing to code.")
    settings = get_settings()
    meter = ProjectMeter(project.id, settings, purpose="qual_suggest", budget_override=project.token_budget_override)
    try:
        llm = OpenAICompatibleLLM.for_participant_data(settings, meter=meter)
        suggestions = qual_suggest.suggest_codes(llm, _accepted_codes(db, project), body.excerpt)
    except LLMBudgetExceeded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except (LLMResponseError, qual_suggest.SuggestionError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    model_id, prompt_version = llm.model, qual_suggest.prompt_ref()
    excerpt = QualExcerpt(project_id=project.id, text=body.excerpt, created_by=audit.user_actor(user))
    db.add(excerpt)
    db.flush()
    rows = [
        QualSuggestion(
            project_id=project.id,
            excerpt_id=excerpt.id,
            code=s.code,
            is_new=s.is_new,
            rationale=s.rationale,
            model_id=model_id,
            prompt_version=prompt_version,
            status="pending",
        )
        for s in suggestions
    ]
    db.add_all(rows)
    audit.record(db, actor=audit.user_actor(user), action="qual.suggested", project_id=project.id,
                 payload={"excerpt_id": str(excerpt.id), "suggestions": len(rows)},
                 model_id=model_id, prompt_version=prompt_version)
    db.commit()
    db.refresh(excerpt)
    return _excerpt_read(excerpt, rows)


@router.get("/excerpts")
def list_excerpts(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    excerpts = db.scalars(select(QualExcerpt).where(QualExcerpt.project_id == project.id).order_by(QualExcerpt.created_at)).all()
    out = []
    for excerpt in excerpts:
        suggestions = db.scalars(
            select(QualSuggestion).where(QualSuggestion.excerpt_id == excerpt.id).order_by(QualSuggestion.created_at)
        ).all()
        out.append(_excerpt_read(excerpt, list(suggestions)))
    return out


@router.post("/decisions")
def decide(
    body: DecisionIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Accept or reject one pending suggestion. A decided suggestion cannot be decided again. Accepting adds the
    code to the project's codebook unless that name is already known (reuse)."""
    row = db.get(QualSuggestion, body.suggestion_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found.")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This suggestion has already been decided.")
    note: str | None = None
    if body.decision == "accept":
        supplied = (body.definition or "").strip()
        if qual_codebook_store.name_is_known(db, project.id, row.code):
            # Reuse (owner, 2026-10-21): no definition needed. A supplied one is not used, and the response says so.
            if supplied:
                note = ("The code is already in the codebook, so the definition you supplied was not used; "
                        "the codebook's existing definition stays.")
        else:
            # The person's definition wins; the model's rationale is the fallback. Refuse with nothing decided if neither exists.
            definition = supplied or (row.rationale or "").strip()
            if not definition:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "This suggestion has no rationale, so write the code's definition to accept it.")
            try:
                qual_codebook_store.accept_code(db, project, user, row.code, definition)
            except ValueError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    row.status = "accepted" if body.decision == "accept" else "rejected"
    row.decided_by = audit.user_actor(user)
    row.decided_at = utcnow()
    audit.record(db, actor=audit.user_actor(user), action="qual.suggestion_decided", project_id=project.id,
                 payload={"suggestion_id": str(row.id), "decision": body.decision, "code": row.code})
    db.commit()
    db.refresh(row)
    return {**_suggestion_read(row), "note": note}


@router.get("/codebook")
def codebook(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """The project's current codebook: live code name -> definition. Renamed-away and merged-away names are
    not listed here (they stay resolvable in the audit log and the kept, un-dropped `QualCode` rows)."""
    return {"codes": qual_codebook_store.active_codes(db, project.id)}


@router.post("/codebook/rename")
def rename_code(
    body: RenameCodeIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        qual_codebook_store.rename_code(db, project, user, body.old, body.new)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return {"codes": qual_codebook_store.active_codes(db, project.id)}


@router.post("/codebook/merge")
def merge_code(
    body: MergeCodeIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        qual_codebook_store.merge_code(db, project, user, body.source, body.target)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return {"codes": qual_codebook_store.active_codes(db, project.id)}
