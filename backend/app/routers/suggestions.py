"""AI suggestions on a project (X.46.2): list them, and record a person's decision on each.

A suggestion changes nothing by itself. Only these endpoints, called by a person with a write role, accept,
edit, discard, restore or undo it, and every call is audited with the suggestion's model and prompt version.
There is no endpoint that creates a suggestion: AI producers do that in code (`suggestion_store`). Gates are
never a suggestion target, so nothing here can approve one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, screening_suggestions, suggestion_store, suggestions
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import AiSuggestion, Project, User

router = APIRouter(prefix="/projects/{project_id}/suggestions", tags=["suggestions"])

MAX_LIST = 200
# Hidden from the default list: set aside by a person, or replaced by a newer answer.
HIDDEN_STATES = frozenset({suggestions.DISCARDED_STATE, suggestions.SUPERSEDED_STATE, suggestions.LOCKED_STATE})


class SuggestionEventRead(BaseModel):
    kind: str
    actor: str
    final_text: str | None
    created_at: datetime


class SuggestionRead(BaseModel):
    id: uuid.UUID
    target_kind: str
    target_id: str
    target_field: str | None
    action: str | None = None  # the workspace AI action that made it (X.31.11); None for other features
    content: str
    confidence: float | None
    model_id: str
    prompt_version: str
    producing_agent: str
    guidance: str | None
    parent_id: uuid.UUID | None
    created_at: datetime
    state: str
    accepted_text: str | None  # what a person made their own; None unless currently accepted
    accepted_by: str | None
    accepted_at: datetime | None
    events: list[SuggestionEventRead]


class SuggestionWhy(BaseModel):
    id: uuid.UUID
    evidence: list[Any]
    confidence: float | None
    model_id: str
    prompt_version: str
    producing_agent: str
    guidance: str | None
    parent_id: uuid.UUID | None


class EditAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=suggestions.MAX_FINAL_TEXT_CHARS)


class AskAgain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guidance: str = Field(min_length=1, max_length=suggestion_store.MAX_GUIDANCE_CHARS)


class BulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept_at_or_above: float | None = Field(default=None, ge=0, le=1)
    discard_below: float | None = Field(default=None, ge=0, le=1)
    target_kind: str | None = Field(default=None, max_length=40)
    target_id: str | None = Field(default=None, max_length=64)


class BulkResult(BaseModel):
    accepted: list[uuid.UUID]
    discarded: list[uuid.UUID]
    skipped: list[uuid.UUID]


def _read(suggestion: AiSuggestion) -> SuggestionRead:
    events = suggestion.events
    state = suggestion_store.state_of(suggestion)
    text = suggestions.accepted_text(suggestion.content, [suggestions.Event(e.kind, e.final_text) for e in events])
    accepting = next((e for e in reversed(events) if e.kind in (suggestions.ACCEPTED, suggestions.EDITED_AND_ACCEPTED)), None)
    accepted = accepting if text is not None else None
    return SuggestionRead(
        id=suggestion.id,
        target_kind=suggestion.target_kind,
        target_id=suggestion.target_id,
        target_field=suggestion.target_field,
        action=suggestion.action,
        content=suggestion.content,
        confidence=suggestion.confidence,
        model_id=suggestion.model_id,
        prompt_version=suggestion.prompt_version,
        producing_agent=suggestion.producing_agent,
        guidance=suggestion.guidance,
        parent_id=suggestion.parent_id,
        created_at=suggestion.created_at,
        state=state,
        accepted_text=text,
        accepted_by=accepted.actor if accepted else None,
        accepted_at=accepted.created_at if accepted else None,
        events=[SuggestionEventRead(kind=e.kind, actor=e.actor, final_text=e.final_text, created_at=e.created_at) for e in events],
    )


def _load(db: Session, project: Project, suggestion_id: uuid.UUID) -> AiSuggestion:
    suggestion = db.scalar(select(AiSuggestion).where(AiSuggestion.id == suggestion_id, AiSuggestion.project_id == project.id))
    if suggestion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found")
    return suggestion


def _query(project: Project, target_kind: str | None, target_id: str | None):
    query = select(AiSuggestion).where(AiSuggestion.project_id == project.id)
    if target_kind:
        query = query.where(AiSuggestion.target_kind == target_kind.strip().lower())
    if target_id:
        query = query.where(AiSuggestion.target_id == target_id)
    return query


def _run(db: Session, action):
    """Run one store call; turn its refusals into HTTP errors and commit on success."""
    try:
        result = action()
        db.commit()
        return result
    except suggestions.InvalidTransition as error:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except suggestion_store.Conflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except suggestion_store.NoProducer as error:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except ValueError as error:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from None


@router.get("", response_model=list[SuggestionRead])
def list_suggestions(
    show_discarded: bool = False,
    target_kind: str | None = Query(default=None, max_length=40),
    target_id: str | None = Query(default=None, max_length=64),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    # Newest first. The page limit counts what is shown, so a pile of discarded or superseded suggestions cannot push
    # a pending one out of the page; hidden rows are skipped as they are met, and the walk stops at a full page.
    query = _query(project, target_kind, target_id).order_by(AiSuggestion.created_at.desc(), AiSuggestion.id)
    shown = []
    for row in db.scalars(query):
        if project.dual_screen_enabled and row.target_kind == screening_suggestions.TARGET_KIND:
            continue  # the screen withholds AI screening suggestions until a reviewer decides (M2.4.1)
        read = _read(row)
        if not show_discarded and read.state in HIDDEN_STATES:
            continue
        shown.append(read)
        if len(shown) == MAX_LIST:
            break
    return shown


@router.get("/{suggestion_id}/why", response_model=SuggestionWhy)
def why(suggestion_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    suggestion = _load(db, project, suggestion_id)
    if project.dual_screen_enabled and suggestion.target_kind == screening_suggestions.TARGET_KIND:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found")
    return SuggestionWhy(
        id=suggestion.id,
        evidence=suggestion.evidence_json or [],
        confidence=suggestion.confidence,
        model_id=suggestion.model_id,
        prompt_version=suggestion.prompt_version,
        producing_agent=suggestion.producing_agent,
        guidance=suggestion.guidance,
        parent_id=suggestion.parent_id,
    )


def _simple(kind: str):
    def endpoint(
        suggestion_id: uuid.UUID,
        project: Project = Depends(project_access(WRITE_ROLES)),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        suggestion = _load(db, project, suggestion_id)
        _run(db, lambda: suggestion_store.act(db, suggestion, kind, audit.user_actor(user)))
        db.refresh(suggestion)
        return _read(suggestion)

    endpoint.__name__ = f"suggestion_{kind}"
    return endpoint


for _path, _kind in (
    ("accept", suggestions.ACCEPTED),
    ("discard", suggestions.DISCARDED),
    ("restore", suggestions.RESTORED),
    ("undo", suggestions.UNDONE),
):
    router.add_api_route(f"/{{suggestion_id}}/{_path}", _simple(_kind), methods=["POST"], response_model=SuggestionRead)


@router.post("/{suggestion_id}/edit-accept", response_model=SuggestionRead)
def edit_and_accept(
    suggestion_id: uuid.UUID,
    body: EditAccept,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    suggestion = _load(db, project, suggestion_id)
    _run(db, lambda: suggestion_store.act(db, suggestion, suggestions.EDITED_AND_ACCEPTED, audit.user_actor(user), text=body.text))
    db.refresh(suggestion)
    return _read(suggestion)


@router.post("/{suggestion_id}/ask-again", response_model=SuggestionRead, status_code=status.HTTP_201_CREATED)
def ask_again(
    suggestion_id: uuid.UUID,
    body: AskAgain,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace a pending suggestion with a new answer; returns the new suggestion."""
    suggestion = _load(db, project, suggestion_id)
    child = _run(db, lambda: suggestion_store.ask_again(db, suggestion, audit.user_actor(user), body.guidance))
    db.refresh(child)
    return _read(child)


@router.post("/bulk", response_model=BulkResult)
def bulk(
    body: BulkRequest,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Accept every pending suggestion at or above one confidence and/or discard those below another.
    Suggestions with no confidence are never touched. All or nothing."""
    actor = audit.user_actor(user)

    def work():
        # No page limit: a bulk action that silently skipped rows past the list's page size would report success
        # while leaving pending suggestions untouched. It is one transaction over every matching suggestion.
        rows = db.scalars(_query(project, body.target_kind, body.target_id).order_by(AiSuggestion.created_at, AiSuggestion.id)).all()
        by_id = {str(row.id): row for row in rows}
        plan = suggestions.plan_bulk(
            ((key, suggestion_store.state_of(row), row.confidence) for key, row in by_id.items()),
            body.accept_at_or_above,
            body.discard_below,
        )
        for key in plan.accept:
            suggestion_store.act(db, by_id[key], suggestions.ACCEPTED, actor)
        for key in plan.discard:
            suggestion_store.act(db, by_id[key], suggestions.DISCARDED, actor)
        return plan

    plan = _run(db, work)
    return BulkResult(
        accepted=[uuid.UUID(key) for key in plan.accept],
        discarded=[uuid.UUID(key) for key in plan.discard],
        skipped=[uuid.UUID(key) for key in plan.skipped],
    )
