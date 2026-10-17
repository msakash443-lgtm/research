from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import active_learning, ai_quality, audit, suggestion_store, suggestions
from app.ai_controls import require_ai_on
from app.artifacts import register_artifact
from app.config import get_settings
from app.database import get_db
from app.lookups import live_source_or_404
from app.dependencies import APPROVE_ROLES, MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access, project_role
from app.models import AiSuggestion, AutoExcludeAudit, Project, ProjectStage, ScreeningDecision, SeedCheckRun, Source, Task, TaskStatus, User
from app.task_handlers import SCREENING_PRESCREEN, SEED_FALSE_EXCLUSION_CHECK
from app.task_queue import enqueue_task
from app.screening import (
    ARBITER,
    HUMAN_DECISIONS,
    STAGES,
    UNDO,
    active_learning_classifier,
    ai_human_agreement,
    ai_suggestion,
    audit_statuses_by_decision,
    autoexclude_threshold,
    check_reason,
    criterion_codes,
    dual_screen_status,
    effective_ai_exclusion,
    final_decision,
    has_any_human_decision,
    human_decisions_by_decider,
    passed_title_abstract,
    rows_for_stage,
    next_seq,
    screening_locked,
    stage_sources,
    stage_agreement,
    visible_rows,
)

SCREENING_KIND = "screening_decision"  # artifact kind for one human screening decision (X.42)

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
    id: uuid.UUID | None = None
    state: str | None = None
    decision: str
    reason_code: str | None
    confidence: float | None
    rationale: str | None
    @model_serializer
    def _serialize(self) -> dict:
        result = {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }
        if self.id is not None:
            result["id"] = self.id
        if self.state is not None:
            result["state"] = self.state
        return result


class QueueItem(BaseModel):
    source_id: uuid.UUID
    title: str
    authors: list | None
    year: int | None
    doi: str | None
    abstract: str | None  # untrusted text from a connector: show it as text only
    fulltext_access: dict | None = None  # full-text stage only: last fetch/upload outcome, so a reviewer can find the PDF
    found_via: dict | None = None  # e.g. snowballing: which paper, direction and round (M1.9.2)
    ai_suggestion: SuggestionRead | None  # a suggestion only; it never counts as a decision


class QueueRead(BaseModel):
    stage: str
    locked: bool
    dual_screen_enabled: bool
    ai_autoexclude_enabled: bool
    sort: str  # "oldest" or "relevance" — the order actually applied (falls back to "oldest" if relevance can't be learned yet)
    counts: dict[str, int]  # include / exclude / maybe / unscreened [/ conflict in dual-screen; / ai_auto_excluded if auto-exclude is on]
    pending: int  # every record waiting for this person, across all pages (not just `items`, which is one page)
    items: list[QueueItem]


class DualScreenRead(BaseModel):
    enabled: bool


class DualScreenUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.get("/dual-screen", response_model=DualScreenRead)
def get_dual_screen(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Whether this project requires two independent reviewers, blinded from each other, before a
    screening decision counts (spec 5.3.3). Off by default."""
    return DualScreenRead(enabled=project.dual_screen_enabled)


@router.put("/dual-screen", response_model=DualScreenRead)
def set_dual_screen(
    payload: DualScreenUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Turn dual-screen mode on or off (owner only). Existing decisions are never rewritten; this only
    changes how new and still-incomplete items are read (M2.4.1)."""
    before = project.dual_screen_enabled
    project.dual_screen_enabled = payload.enabled
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="screening.dual_screen_changed", project_id=project.id,
        payload={"from": before, "to": payload.enabled},
    )
    db.commit()
    db.refresh(project)
    return DualScreenRead(enabled=project.dual_screen_enabled)


class AutoExcludeRead(BaseModel):
    enabled: bool
    confidence_threshold: float  # effective threshold: the project's own override, else the global default
    confidence_threshold_override: float | None  # None if the project uses the global default
    sample_rate: float  # global, not per-project configurable


class AutoExcludeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)  # None clears the override


def _auto_exclude_read(project: Project) -> AutoExcludeRead:
    settings = get_settings()
    return AutoExcludeRead(
        enabled=project.ai_autoexclude_enabled,
        confidence_threshold=autoexclude_threshold(project, settings.screening_ai_autoexclude_confidence_threshold),
        confidence_threshold_override=project.ai_autoexclude_confidence_threshold,
        sample_rate=settings.screening_ai_autoexclude_sample_rate,
    )


@router.get("/auto-exclude", response_model=AutoExcludeRead)
def get_auto_exclude(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Whether a high-confidence AI exclude at title/abstract can count without a person deciding
    that record individually, as long as the human sampling audit keeps confirming it (spec 5.3
    human gate, M2.5.1). Off by default."""
    return _auto_exclude_read(project)


@router.put("/auto-exclude", response_model=AutoExcludeRead)
def set_auto_exclude(
    payload: AutoExcludeUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Turn AI auto-exclude on or off, and optionally override this project's confidence threshold
    (owner only). Never rewrites past decisions; only changes how unscreened sources are read."""
    before = {"enabled": project.ai_autoexclude_enabled, "confidence_threshold": project.ai_autoexclude_confidence_threshold}
    project.ai_autoexclude_enabled = payload.enabled
    project.ai_autoexclude_confidence_threshold = payload.confidence_threshold
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="screening.autoexclude_changed", project_id=project.id,
        payload={"from": before, "to": {"enabled": payload.enabled, "confidence_threshold": payload.confidence_threshold}},
    )
    db.commit()
    db.refresh(project)
    return _auto_exclude_read(project)


class ActiveLearningRead(BaseModel):
    enabled: bool


class ActiveLearningUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.get("/active-learning", response_model=ActiveLearningRead)
def get_active_learning(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """Whether `GET .../queue?sort=relevance` may rank unscreened records by predicted relevance
    (spec 5.3.2, M2.6), learned fresh each request from the project's current human decisions. Off
    by default; with no decisions of both kinds yet, `sort=relevance` falls back to oldest-first."""
    return ActiveLearningRead(enabled=project.active_learning_enabled)


@router.put("/active-learning", response_model=ActiveLearningRead)
def set_active_learning(
    payload: ActiveLearningUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Turn active-learning prioritisation on or off (owner only). Changes only how `sort=relevance`
    is read; never rewrites a decision or the oldest-first default."""
    before = project.active_learning_enabled
    project.active_learning_enabled = payload.enabled
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="screening.active_learning_changed", project_id=project.id,
        payload={"from": before, "to": payload.enabled},
    )
    db.commit()
    db.refresh(project)
    return ActiveLearningRead(enabled=project.active_learning_enabled)
@router.get("/queue", response_model=QueueRead)

def get_queue(
    stage: str = Query("title_abstract"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("oldest"),
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The sources still waiting for *this person's* decision at this stage, with the stage's counts.

    In dual-screen mode (spec 5.3.3) a source stays in a reviewer's own queue until *they* decide it,
    even if another reviewer already has — and its AI suggestion is withheld until then, so neither
    reviewer is biased by the other's (or the AI's) answer. Two reviewers who agree count as decided;
    disagreement is a `conflict` (M2.4.2b resolves it), not a decision.

    `sort`: `"oldest"` (default) or `"relevance"` (M2.6, spec 5.3.2) — ranks by a classifier trained
    fresh from this project's current human decisions, highest predicted relevance first. Only applies
    when `active_learning_enabled` is on, dual-screen is off (ranking would leak the other reviewer's
    decisions through order) and both decision classes exist yet; otherwise falls back to
    oldest-first (the response's `sort` field says which was actually used, never silently different
    from what was asked without saying so).
    """
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    if sort not in ("oldest", "relevance"):
        raise HTTPException(status_code=422, detail="sort must be one of: oldest, relevance")
    grouped = rows_for_stage(db, project, stage)
    counts = {"include": 0, "exclude": 0, "maybe": 0, "unscreened": 0}
    if project.dual_screen_enabled:
        counts["conflict"] = 0
    if project.ai_autoexclude_enabled:
        counts["ai_auto_excluded"] = 0
    settings = get_settings()
    threshold = autoexclude_threshold(project, settings.screening_ai_autoexclude_confidence_threshold)
    audit_statuses = audit_statuses_by_decision(db, project) if project.ai_autoexclude_enabled else {}
    my_decider = audit.user_actor(user)
    waiting: list[Source] = []
    for source in stage_sources(db, project, stage):
        rows = grouped.get(source.id, [])
        if project.dual_screen_enabled:
            status_, rep = dual_screen_status(rows)
            if status_ in ("agreed", "adjudicated"):
                counts[rep.decision] += 1
            elif status_ == "conflict":
                counts["conflict"] += 1
            else:
                counts["unscreened"] += 1
            if my_decider not in human_decisions_by_decider(rows):
                waiting.append(source)
        else:
            final = final_decision(rows)
            if final is not None:
                counts[final.decision] += 1
                continue
            if effective_ai_exclusion(rows, audit_statuses, enabled=project.ai_autoexclude_enabled, threshold=threshold, prescreen_disabled=project.ai_prescreen_disabled) is not None:
                counts["ai_auto_excluded"] += 1
                continue
            counts["unscreened"] += 1
            waiting.append(source)
    applied_sort = "oldest"
    if sort == "relevance" and project.active_learning_enabled and not project.dual_screen_enabled:
        classifier = active_learning_classifier(db, project, stage)
        if classifier is not None:
            waiting = active_learning.rank_by_relevance(classifier, waiting, lambda s: f"{s.title or ''} {s.abstract or ''}")
            applied_sort = "relevance"
    suggestion_rows = {
        row.target_id: row
        for row in db.scalars(
            select(AiSuggestion).where(
                AiSuggestion.project_id == project.id,
                AiSuggestion.target_kind == "screening",
                AiSuggestion.target_id.in_([str(source.id) for source in waiting[offset : offset + limit]]),
            )
        ).all()
    }
    items = []
    for source in waiting[offset : offset + limit]:
        rows = grouped.get(source.id, [])
        suggestion = None if project.dual_screen_enabled else ai_suggestion(rows)
        shared = None if project.dual_screen_enabled else suggestion_rows.get(str(source.id))
        items.append(
            QueueItem(
                source_id=source.id, title=source.title, authors=source.authors, year=source.year, doi=source.doi,
                abstract=source.abstract, fulltext_access=source.fulltext_access if stage == "full_text" else None,
                found_via=source.found_via,
                ai_suggestion=SuggestionRead(
                    id=shared.id if shared else None,
                    state=suggestions.state_of(event.kind for event in shared.events) if shared else None,
                    decision=suggestion.decision if suggestion else "",
                    reason_code=suggestion.reason_code if suggestion else None,
                    confidence=suggestion.confidence if suggestion else None,
                    rationale=suggestion.note if suggestion else None,
                ) if suggestion or shared else None,
            )
        )
    return QueueRead(
        stage=stage, locked=screening_locked(db, project), dual_screen_enabled=project.dual_screen_enabled,
        ai_autoexclude_enabled=project.ai_autoexclude_enabled, sort=applied_sort, counts=counts, pending=len(waiting), items=items,
    )


@router.get("/decisions", response_model=list[DecisionRead])
def list_decisions(
    source_id: uuid.UUID | None = None,
    stage: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The decision history, newest first: every decision and undo, human and AI.

    In dual-screen mode (spec 5.3.3) a row by someone else is blinding-withheld for a source+stage
    until the caller has their own current decision there too (M2.4.1).
    """
    if stage is not None and stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    query = select(ScreeningDecision).where(ScreeningDecision.project_id == project.id)
    if source_id:
        query = query.where(ScreeningDecision.source_id == source_id)
    if stage:
        query = query.where(ScreeningDecision.stage == stage)
    rows = list(db.scalars(query.order_by(ScreeningDecision.seq)))  # oldest first, for grouping
    if project.dual_screen_enabled:
        rows = visible_rows(rows, audit.user_actor(user))
    rows.sort(key=lambda r: (r.decided_at, r.seq), reverse=True)
    return rows[:limit]


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
    full_text_rows = rows_for_stage(db, project, "full_text").get(source.id, [])
    if project.dual_screen_enabled:
        return has_any_human_decision(full_text_rows)
    return final_decision(full_text_rows) is not None


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
    source = live_source_or_404(db, project, payload.source_id)
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
    db.flush()  # the row's id is the ref: each decision is its own artifact
    register_artifact(db, project.id, SCREENING_KIND, str(row.id), ProjectStage.screened, actor=audit.user_actor(user))
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
    source = live_source_or_404(db, project, payload.source_id)
    rows = rows_for_stage(db, project, payload.stage).get(source.id, [])
    if project.dual_screen_enabled:
        if audit.user_actor(user) not in human_decisions_by_decider(rows):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have no decision of your own to undo for this source.")
    elif final_decision(rows) is None:
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


@router.post("/decisions/adjudicate", response_model=DecisionRead, status_code=status.HTTP_201_CREATED)
def adjudicate(
    payload: DecisionIn,
    project: Project = Depends(project_access(APPROVE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Rule on a reviewer conflict (owner or supervisor, M2.4.3). Only a source whose reviewers currently disagree
    can be ruled on, and only under dual-screen. The ruling is a new row (`decided_by="arbiter"`); both reviewers'
    rows stay as they are, so agreement and kappa still compare what the two of them decided. Someone who has a
    decision of their own on this source cannot rule on it. A reviewer who changes or undoes their decision
    afterwards reopens the conflict. Ruling again replaces an earlier ruling (the earlier row stays)."""
    if screening_locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    if not project.dual_screen_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dual screening is off, so there are no reviewer conflicts to rule on.")
    source = live_source_or_404(db, project, payload.source_id)
    rows = rows_for_stage(db, project, payload.stage).get(source.id, [])
    if dual_screen_status(rows)[0] not in ("conflict", "adjudicated"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a source whose reviewers disagree can be ruled on.")
    actor = audit.user_actor(user)
    if actor in human_decisions_by_decider(rows):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You decided this source yourself, so you can't rule on it.")
    problem = check_reason(payload.decision, payload.reason_code, criterion_codes(db, project))
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
    if payload.stage == "full_text" and source.id not in passed_title_abstract(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source has not passed title/abstract screening, so it can't be screened at full text.")
    if _blocked_by_full_text(db, project, source, payload.stage, payload.decision):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This source already has a full-text decision. Undo that first.")
    note = payload.note.strip() if payload.note and payload.note.strip() else None
    row = ScreeningDecision(
        project_id=project.id, source_id=source.id, stage=payload.stage, seq=next_seq(db, source.id, payload.stage),
        decision=payload.decision, reason_code=payload.reason_code, decided_by=ARBITER, decider=actor, note=note,
    )
    db.add(row)
    audit.record(
        db, actor=actor, action="screening.adjudicated", project_id=project.id,
        payload={"source_id": str(source.id), "stage": payload.stage, "decision": payload.decision, "reason_code": payload.reason_code},
    )
    db.flush()  # the row's id is the ref: each ruling is its own artifact
    register_artifact(db, project.id, SCREENING_KIND, str(row.id), ProjectStage.screened, actor=actor)
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
    Asking again while one is already waiting or running returns that task. Refused while AI pre-screen
    is disabled for this project (M2.5.2), whether automatically (agreement dropped too low) or by the owner.
    """
    require_ai_on(project)
    if project.ai_prescreen_disabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=project.ai_prescreen_disabled_reason or "AI pre-screen is disabled for this project.",
        )
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


class SeedCheckQueued(BaseModel):
    task_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    blocked_by_gate: str | None


_OPEN_TASK = (TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused)


@router.post("/seed-check", response_model=SeedCheckQueued, status_code=status.HTTP_202_ACCEPTED)
def request_seed_check(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Start a seed-based false-exclusion check (M2.9.1): the AI pre-screen answers for each seed paper that
    matches a stored source, and how many it would exclude is reported against the 2% limit.

    A rough check, not proof, and not screening: it writes no screening decision, so nothing here reaches the
    queue or PRISMA. Needs gate G2 like pre-screening (the task waits until a person approves it). Asking
    again while one is waiting or running returns that task.
    """
    require_ai_on(project)
    open_task = db.scalar(
        select(Task).where(Task.project_id == project.id, Task.type == SEED_FALSE_EXCLUSION_CHECK, Task.status.in_(_OPEN_TASK))
    )
    actor = audit.user_actor(user)
    if open_task is not None:
        task, run_id = open_task, uuid.UUID(str(open_task.payload["run_id"]))
    else:
        run_id = uuid.uuid4()
        task = enqueue_task(
            db, project.id, SEED_FALSE_EXCLUSION_CHECK,
            payload={"project_id": str(project.id), "run_id": str(run_id), "actor": actor},
            actor=actor, idempotency_key=f"seed_check:{run_id}",
        )
        audit.record(db, actor=actor, action="screening.seed_check_requested", project_id=project.id,
                     payload={"task_id": str(task.id), "run_id": str(run_id)})
    db.commit()
    return SeedCheckQueued(
        task_id=task.id, run_id=run_id, status=task.status.value,
        blocked_by_gate=task.blocked_by_gate.value if task.blocked_by_gate else None,
    )


class SeedCheckRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    created_by: str
    model_id: str
    prompt_version: str
    min_confidence: float
    autoexclude_threshold: float
    summary: dict
    entries: list[dict]


class SeedCheckRead(BaseModel):
    latest: SeedCheckRunRead | None
    pending: SeedCheckQueued | None


@router.get("/seed-check", response_model=SeedCheckRead)
def get_seed_check(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """The latest seed-based false-exclusion check, and the one still waiting or running, if any."""
    latest = db.scalar(
        select(SeedCheckRun).where(SeedCheckRun.project_id == project.id)
        .order_by(SeedCheckRun.created_at.desc(), SeedCheckRun.id.desc())
    )
    open_task = db.scalar(
        select(Task).where(Task.project_id == project.id, Task.type == SEED_FALSE_EXCLUSION_CHECK, Task.status.in_(_OPEN_TASK))
    )
    pending = None
    if open_task is not None:
        pending = SeedCheckQueued(
            task_id=open_task.id, run_id=uuid.UUID(str(open_task.payload["run_id"])), status=open_task.status.value,
            blocked_by_gate=open_task.blocked_by_gate.value if open_task.blocked_by_gate else None,
        )
    return SeedCheckRead(latest=SeedCheckRunRead.model_validate(latest) if latest else None, pending=pending)


class ReviewerDecisionRead(BaseModel):
    decider: str
    decision: str
    reason_code: str | None
    decided_at: datetime


class ConflictItem(BaseModel):
    source_id: uuid.UUID
    title: str
    decisions: list[ReviewerDecisionRead]  # every current reviewer's decision, oldest decider first
    adjudication: ReviewerDecisionRead | None = None  # the owner's/supervisor's ruling that settles it, if any (M2.4.3)


class AgreementScope(BaseModel):
    stage_sources: int  # every source eligible for this stage
    no_reviewer: int  # nobody has decided it yet
    one_reviewer: int  # exactly one reviewer so far: not comparable yet
    two_reviewers: int  # exactly two: counted in kappa
    three_or_more_reviewers: int  # more than two deciders exist (e.g. two owners): not a 2-rater kappa item, still in `conflicts` if they disagree


class AgreementRead(BaseModel):
    stage: str
    n: int  # items compared (exactly two reviewers)
    agreed: int
    percent_agreement: float | None
    kappa: float | None
    band: str | None
    categories: list[str]
    matrix: dict[str, dict[str, int]]
    note: str | None
    scope: AgreementScope
    conflicts: list[ConflictItem]  # every source where the current reviewers don't all agree, oldest first


@router.get("/agreement", response_model=AgreementRead)
def get_agreement(
    stage: str = Query("title_abstract"),
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Inter-rater agreement (Cohen's kappa, spec 5.3.3) and the conflict queue for a third reviewer.

    Compares every source at this stage that currently has decisions from exactly two distinct human
    reviewers (kappa is a two-rater statistic); a source with only one reviewer so far, or more than
    two (e.g. a project with two owners), isn't counted in kappa but a 3+-reviewer disagreement still
    shows in `conflicts`. Works whether or not dual-screen mode (M2.4.1) is on. In dual-screen mode a
    conflict on a record is listed only to a caller who has decided that record too (M2.3.3).
    """
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    # Blinding protects people who can still screen. Someone who cannot screen (a supervisor, a reviewer) is not
    # biased by seeing a conflict, and a supervisor needs to see it to rule on it (M2.4.3; owner decision 2026-10-12).
    can_screen = project_role(db, project, user) in WRITE_ROLES
    result = stage_agreement(db, project, stage, viewer_decider=audit.user_actor(user) if can_screen else None)
    a = result.agreement
    return AgreementRead(
        stage=stage, n=a.n, agreed=a.agreed, percent_agreement=a.percent_agreement,
        kappa=a.kappa, band=a.band, categories=list(a.categories), matrix=a.matrix, note=a.note,
        scope=AgreementScope(**result.scope),
        conflicts=[
            ConflictItem(
                source_id=c["source_id"], title=c["title"], decisions=[ReviewerDecisionRead(**d) for d in c["decisions"]],
                adjudication=ReviewerDecisionRead(**c["adjudication"]) if c["adjudication"] else None,
            )
            for c in result.conflicts
        ],
    )


class AutoExcludeAuditRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    title: str
    stage: str
    confidence: float
    status: str  # pending | confirmed | overturned
    reviewed_by: str | None
    reviewed_at: datetime | None
    note: str | None
    created_at: datetime


@router.get("/auto-exclude/audits", response_model=list[AutoExcludeAuditRead])
def list_auto_exclude_audits(
    status_filter: str | None = Query(None, alias="status"),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """The human sampling audit queue for AI auto-excludes (spec 5.3 human gate, M2.5.1), newest first.
    `status` filters to `pending`/`confirmed`/`overturned`; omitted shows all."""
    if status_filter is not None and status_filter not in ("pending", "confirmed", "overturned"):
        raise HTTPException(status_code=422, detail="status must be one of: pending, confirmed, overturned")
    query = select(AutoExcludeAudit, Source.title).join(Source, Source.id == AutoExcludeAudit.source_id).where(
        AutoExcludeAudit.project_id == project.id
    )
    if status_filter is not None:
        query = query.where(AutoExcludeAudit.status == status_filter)
    rows = db.execute(query.order_by(AutoExcludeAudit.created_at.desc())).all()
    return [
        AutoExcludeAuditRead(
            id=a.id, source_id=a.source_id, title=title, stage=a.stage, confidence=a.confidence, status=a.status,
            reviewed_by=a.reviewed_by, reviewed_at=a.reviewed_at, note=a.note, created_at=a.created_at,
        )
        for a, title in rows
    ]


class AutoExcludeAuditReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str  # "confirm" | "overturn"
    note: str | None = None

    @field_validator("outcome")
    @classmethod
    def _check_outcome(cls, value: str) -> str:
        if value not in ("confirm", "overturn"):
            raise ValueError("outcome must be 'confirm' or 'overturn'")
        return value


@router.post("/auto-exclude/audits/{audit_id}/review", response_model=AutoExcludeAuditRead)
def review_auto_exclude_audit(
    audit_id: uuid.UUID,
    payload: AutoExcludeAuditReview,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Confirm or overturn one sampled AI auto-exclude. Confirming keeps it excluded; overturning
    returns the source to the normal screening queue for a person to decide (spec 5.3 human gate).
    A row can be reviewed once; reviewing it again is refused."""
    row = db.scalar(select(AutoExcludeAudit).where(AutoExcludeAudit.id == audit_id, AutoExcludeAudit.project_id == project.id))
    if row is None:
        raise HTTPException(status_code=404, detail="No such audit item.")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"This item was already reviewed ({row.status}).")
    result = db.execute(
        update(AutoExcludeAudit)
        .where(AutoExcludeAudit.id == audit_id, AutoExcludeAudit.status == "pending")
        .values(
            status="confirmed" if payload.outcome == "confirm" else "overturned",
            reviewed_by=audit.user_actor(user),
            reviewed_at=datetime.now(timezone.utc),
            note=payload.note,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="This item was already reviewed.")
    source_title = db.scalar(select(Source.title).where(Source.id == row.source_id))
    audit.record(
        db, actor=audit.user_actor(user), action="screening.autoexclude_reviewed", project_id=project.id,
        payload={"audit_id": str(row.id), "source_id": str(row.source_id), "outcome": payload.outcome},
    )
    db.commit()
    db.refresh(row)
    return AutoExcludeAuditRead(
        id=row.id, source_id=row.source_id, title=source_title or "", stage=row.stage, confidence=row.confidence,
        status=row.status, reviewed_by=row.reviewed_by, reviewed_at=row.reviewed_at, note=row.note, created_at=row.created_at,
    )


class AIAgreementRead(BaseModel):
    stage: str
    n: int
    agreed: int
    percent_agreement: float | None
    kappa: float | None
    band: str | None
    note: str | None
    min_n: int
    min_kappa: float
    prescreen_disabled: bool
    prescreen_disabled_reason: str | None


@router.get("/ai-agreement", response_model=AIAgreementRead)
def get_ai_agreement(
    stage: str = Query("title_abstract"),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Agreement (Cohen's kappa) between the AI's suggestion and the person's own eventual decision,
    over every source where both exist at this stage (spec 5.3 quality checks, M2.5.2). Pre-screen
    auto-disables when this drops below the configured floor; see `prescreen_disabled`."""
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    settings = get_settings()
    agreement = ai_human_agreement(db, project, stage)
    return AIAgreementRead(
        stage=stage, n=agreement.n, agreed=agreement.agreed, percent_agreement=agreement.percent_agreement,
        kappa=agreement.kappa, band=agreement.band, note=agreement.note,
        min_n=settings.screening_ai_agreement_min_n, min_kappa=settings.screening_ai_agreement_min_kappa,
        prescreen_disabled=project.ai_prescreen_disabled, prescreen_disabled_reason=project.ai_prescreen_disabled_reason,
    )


class AIPrescreenStatusRead(BaseModel):
    disabled: bool
    reason: str | None


class AIPrescreenStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled: bool


@router.put("/ai-prescreen", response_model=AIPrescreenStatusRead)
def set_ai_prescreen_status(
    payload: AIPrescreenStatusUpdate,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Manually disable AI pre-screen, or re-enable it after the automatic guard (M2.5.2) tripped it
    (owner only). Re-enabling clears the recorded reason."""
    before = {"disabled": project.ai_prescreen_disabled, "reason": project.ai_prescreen_disabled_reason}
    project.ai_prescreen_disabled = payload.disabled
    project.ai_prescreen_disabled_reason = None if not payload.disabled else (project.ai_prescreen_disabled_reason or "Disabled by the project owner.")
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="screening.ai_prescreen_disabled_changed", project_id=project.id,
        payload={"from": before, "to": {"disabled": project.ai_prescreen_disabled, "reason": project.ai_prescreen_disabled_reason}},
    )
    db.commit()
    db.refresh(project)
    return AIPrescreenStatusRead(disabled=project.ai_prescreen_disabled, reason=project.ai_prescreen_disabled_reason)
