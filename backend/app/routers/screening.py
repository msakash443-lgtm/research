from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, ScreeningDecision, Source, Task, TaskStatus, User
from app.task_handlers import SCREENING_PRESCREEN
from app.task_queue import enqueue_task
from app.screening import (
    HUMAN_DECISIONS,
    STAGES,
    UNDO,
    ai_suggestion,
    check_reason,
    criterion_codes,
    final_decision,
    passed_title_abstract,
    rows_for_stage,
    screenable_sources,
    next_seq,
    screening_locked,
)

router = APIRouter(prefix="/projects/{project_id}/screening", tags=["screening"])

LOCKED_MESSAGE = "Gate G3 is approved for this screening. Reopen the project at the screening stage to change a decision."
STAGE_MESSAGE = "stage must be one of: " + ", ".join(STAGES)


def _check_stage(value: str) -> str:
    if value not in STAGES:
        raise ValueError(STAGE_MESSAGE)
    return value


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    stage: str = "title_abstract"
    decision: str
    reason_code: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: str) -> str:
        return _check_stage(value)

    @field_validator("decision")
    @classmethod
    def _decision(cls, value: str) -> str:
        if value not in HUMAN_DECISIONS:
            raise ValueError("decision must be one of: " + ", ".join(HUMAN_DECISIONS))
        return value


class UndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    stage: str = "title_abstract"

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: str) -> str:
        return _check_stage(value)


class DecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    stage: str
    seq: int
    decision: str
    reason_code: str | None
    decided_by: str
    decider: str
    confidence: float | None
    note: str | None
    decided_at: datetime


class SuggestionRead(BaseModel):
    decision: str
    reason_code: str | None
    confidence: float | None
    rationale: str | None


class QueueItem(BaseModel):
    source_id: uuid.UUID
    title: str
    authors: list | None
    year: int | None
    doi: str | None
    abstract: str | None  # untrusted text from a connector: show it as text only
    found_via: dict | None = None  # e.g. snowballing: which paper, direction and round (M1.9.2)
    ai_suggestion: SuggestionRead | None  # a suggestion only; it never counts as a decision


class QueueRead(BaseModel):
    stage: str
    locked: bool
    counts: dict[str, int]  # include / exclude / maybe / unscreened, over the sources in this stage
    items: list[QueueItem]


def _stage_sources(db: Session, project: Project, stage: str) -> list[Source]:
    sources = screenable_sources(db, project)
    if stage == "full_text":
        passed = passed_title_abstract(db, project)
        sources = [s for s in sources if s.id in passed]
    return sources


@router.get("/queue", response_model=QueueRead)
def get_queue(
    stage: str = Query("title_abstract"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """The sources still waiting for a person's decision at this stage, oldest first, with the stage's counts."""
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    grouped = rows_for_stage(db, project, stage)
    counts = {"include": 0, "exclude": 0, "maybe": 0, "unscreened": 0}
    waiting: list[Source] = []
    for source in _stage_sources(db, project, stage):
        final = final_decision(grouped.get(source.id, []))
        if final is None:
            counts["unscreened"] += 1
            waiting.append(source)
        else:
            counts[final.decision] += 1
    items = []
    for source in waiting[offset : offset + limit]:
        suggestion = ai_suggestion(grouped.get(source.id, []))
        items.append(
            QueueItem(
                source_id=source.id, title=source.title, authors=source.authors, year=source.year, doi=source.doi,
                abstract=source.abstract, found_via=source.found_via,
                ai_suggestion=SuggestionRead(decision=suggestion.decision, reason_code=suggestion.reason_code, confidence=suggestion.confidence, rationale=suggestion.note) if suggestion else None,
            )
        )
    return QueueRead(stage=stage, locked=screening_locked(db, project), counts=counts, items=items)


@router.get("/decisions", response_model=list[DecisionRead])
def list_decisions(
    source_id: uuid.UUID | None = None,
    stage: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """The decision history, newest first: every decision and undo, human and AI."""
    if stage is not None and stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    query = select(ScreeningDecision).where(ScreeningDecision.project_id == project.id)
    if source_id:
        query = query.where(ScreeningDecision.source_id == source_id)
    if stage:
        query = query.where(ScreeningDecision.stage == stage)
    return list(db.scalars(query.order_by(ScreeningDecision.decided_at.desc(), ScreeningDecision.seq.desc()).limit(limit)))


def _source_or_404(db: Session, project: Project, source_id: uuid.UUID) -> Source:
    source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project.id, Source.merged_into.is_(None)))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source


def _append(db: Session, project: Project, source: Source, user: User, stage: str, decision: str, reason_code: str | None, note: str | None) -> ScreeningDecision:
    row = ScreeningDecision(
        project_id=project.id, source_id=source.id, stage=stage, seq=next_seq(db, source.id, stage), decision=decision,
        reason_code=reason_code, decided_by="human", decider=audit.user_actor(user), note=note,
    )
    db.add(row)
    return row


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:  # two people decided the same source at the same moment
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Someone else just decided this source. Reload and try again.") from exc


def _blocked_by_full_text(db: Session, project: Project, source: Source, stage: str, decision: str) -> bool:
    """A title/abstract change that would drop a source from full text while a person already screened it there."""
    if stage != "title_abstract" or decision in ("include", "maybe"):
        return False
    return final_decision(rows_for_stage(db, project, "full_text").get(source.id, [])) is not None


@router.post("/decisions", response_model=DecisionRead, status_code=status.HTTP_201_CREATED)
def decide(
    payload: DecisionIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Record a person's decision. It replaces their earlier one (the earlier row stays in the history)."""
    if screening_locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    source = _source_or_404(db, project, payload.source_id)
    problem = check_reason(payload.decision, payload.reason_code, criterion_codes(db, project))
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
    if payload.stage == "full_text" and source.id not in passed_title_abstract(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source has not passed title/abstract screening, so it can't be screened at full text.")
    if _blocked_by_full_text(db, project, source, payload.stage, payload.decision):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source already has a full-text decision. Undo that first.")
    note = payload.note.strip() if payload.note and payload.note.strip() else None
    row = _append(db, project, source, user, payload.stage, payload.decision, payload.reason_code, note)
    audit.record(
        db, actor=audit.user_actor(user), action="screening.decided", project_id=project.id,
        payload={"source_id": str(source.id), "stage": payload.stage, "decision": payload.decision, "reason_code": payload.reason_code, "decided_by": "human"},
    )
    _commit(db)
    db.refresh(row)
    return row


@router.post("/decisions/undo", response_model=DecisionRead, status_code=status.HTTP_201_CREATED)
def undo(
    payload: UndoIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Take a person's decision back: the source returns to the queue. Appends a row; nothing is deleted."""
    if screening_locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    source = _source_or_404(db, project, payload.source_id)
    if final_decision(rows_for_stage(db, project, payload.stage).get(source.id, [])) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="There is no decision to undo for this source.")
    if _blocked_by_full_text(db, project, source, payload.stage, UNDO):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source already has a full-text decision. Undo that first.")
    row = _append(db, project, source, user, payload.stage, UNDO, None, None)
    audit.record(
        db, actor=audit.user_actor(user), action="screening.undone", project_id=project.id,
        payload={"source_id": str(source.id), "stage": payload.stage, "decided_by": "human"},
    )
    _commit(db)
    db.refresh(row)
    return row


class PrescreenQueued(BaseModel):
    task_id: uuid.UUID
    status: str
    blocked_by_gate: str | None


@router.post("/prescreen", response_model=PrescreenQueued, status_code=status.HTTP_202_ACCEPTED)
def request_prescreen(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Ask the AI for suggestions on the unscreened sources. Suggestions only; a person still decides each one.

    Needs gate G2 (the criteria it uses are the approved ones): until a person approves it the task waits.
    Asking again while one is already waiting or running returns that task.
    """
    open_task = db.scalar(
        select(Task).where(
            Task.project_id == project.id, Task.type == SCREENING_PRESCREEN,
            Task.status.in_((TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused)),
        )
    )
    actor = audit.user_actor(user)
    task = open_task or enqueue_task(db, project.id, SCREENING_PRESCREEN, payload={"project_id": str(project.id), "actor": actor}, actor=actor)
    if open_task is None:
        audit.record(db, actor=actor, action="screening.prescreen_requested", project_id=project.id, payload={"task_id": str(task.id)})
    db.commit()
    return PrescreenQueued(task_id=task.id, status=task.status.value, blocked_by_gate=task.blocked_by_gate.value if task.blocked_by_gate else None)
