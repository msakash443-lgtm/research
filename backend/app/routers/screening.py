from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import active_learning, ai_quality, audit, fulltext_coverage, question_match, suggestion_store, suggestions
from app.ai_controls import require_ai_on
from app.artifacts import register_artifact
from app.config import get_settings
from app.database import get_db
from app.lookups import live_source_or_404
from app.dependencies import APPROVE_ROLES, MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access, project_role
from app.prescreen import confidence_level
from app.source_flags import is_preprint
from app.models import AiSuggestion, AutoExcludeAudit, Project, ProjectRole, ProjectStage, ResearchQuestion, ScreeningCriterion, ScreeningDecision, ScreeningDiscussionNote, SeedCheckRun, Source, Task, TaskStatus, User
from app.task_handlers import FULLTEXT_FETCH, SCREENING_PRESCREEN, SEED_FALSE_EXCLUSION_CHECK
from app.arxiv_fulltext import ARXIV_FULLTEXT_FETCH
from app.connectors.access import is_enabled
from app.connectors.factory import unpaywall_unavailable_reason
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
    included_sources,
    settled_decision,
    autoexclude_threshold,
    check_reason,
    criterion_codes,
    current_discussion,
    dual_screen_status,
    effective_ai_exclusion,
    final_decision,
    has_any_human_decision,
    has_own_decision,
    has_taken_part,
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
    evidence_quote: str | None = None  # words of the title/abstract the AI says decided it, checked to be there (X.77)
    evidence_source: str | None = None  # "title" or "abstract"
    @model_serializer
    def _serialize(self) -> dict:
        result = {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }
        if self.confidence is not None:
            result["confidence_level"] = confidence_level(self.confidence)
        if self.evidence_quote:
            result["evidence_quote"] = self.evidence_quote
            result["evidence_source"] = self.evidence_source
        if self.id is not None:
            result["id"] = self.id
        if self.state is not None:
            result["state"] = self.state
        return result


def _stored_evidence(shared: AiSuggestion | None) -> dict[str, str]:
    """The quote the pre-screen stored next to its suggestion, when it gave one (second evidence item, X.77)."""
    for item in (shared.evidence_json or [])[1:] if shared else []:
        if isinstance(item, dict) and isinstance(item.get("quote"), str) and item["quote"]:
            return {"evidence_quote": item["quote"], "evidence_source": item.get("source") if isinstance(item.get("source"), str) else "abstract"}
    return {}


class QueueItem(BaseModel):
    source_id: uuid.UUID
    title: str
    authors: list | None
    year: int | None
    doi: str | None
    abstract: str | None  # untrusted text from a connector: show it as text only
    fulltext_access: dict | None = None  # full-text stage only: last fetch/upload outcome, so a reviewer can find the PDF
    found_via: dict | None = None  # e.g. snowballing: which paper, direction and round (M1.9.2)
    coverage: dict | None = None  # full text / abstract only / not accessible (X.76)
    matched_terms: list[str] = []  # question-match terms matched in title+abstract (X.199); empty unless sort=question_match
    preprint: bool = False  # a service flagged this a preprint: not peer reviewed (X.196)


class RevealedSuggestion(BaseModel):
    """The AI's suggestion for a source the caller has already decided themselves (X.224)."""
    your_decision: str
    your_reason_code: str | None
    suggestion: SuggestionRead | None  # None: the AI gave no suggestion for this source
    agrees: bool | None  # your decision and reason match the suggestion; None when there is none


class QueueRead(BaseModel):
    stage: str
    locked: bool
    dual_screen_enabled: bool
    ai_autoexclude_enabled: bool
    sort: str  # "oldest", "relevance" or "question_match" — the order actually applied (falls back to "oldest" if the requested sort can't be applied)
    sort_note: str | None = None  # explains a fallback away from the requested sort, e.g. no research questions or criteria yet
    counts: dict[str, int]  # include / exclude / maybe / unscreened [/ conflict in dual-screen; / ai_auto_excluded if auto-exclude is on]
    pending: int  # every record waiting for this person, across all pages (not just `items`, which is one page)
    peer_reviewed_only: bool = False  # X.196: preprints are listed separately and not citable in synthesis
    preprint_pending: int = 0  # of `pending`, how many are preprint-flagged
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
class PeerReviewedOnlyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class PeerReviewedOnlyRead(BaseModel):
    enabled: bool


@router.get("/peer-reviewed-only", response_model=PeerReviewedOnlyRead)
def get_peer_reviewed_only(project: Project = Depends(project_access(READ_ROLES))):
    """Whether preprint-flagged sources are barred from synthesis citations (X.196). Off by default."""
    return PeerReviewedOnlyRead(enabled=project.peer_reviewed_only)


@router.put("/peer-reviewed-only", response_model=PeerReviewedOnlyRead)
def set_peer_reviewed_only(
    payload: PeerReviewedOnlyBody,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Turn peer-reviewed-only on or off (owner/manage roles). On: a synthesis run that cites a preprint is
    rejected and screening lists preprints separately. It never rewrites a decision or a stored source."""
    before = project.peer_reviewed_only
    project.peer_reviewed_only = payload.enabled
    project.touch()
    audit.record(
        db, actor=audit.user_actor(user), action="screening.peer_reviewed_only_changed", project_id=project.id,
        payload={"from": before, "to": payload.enabled},
    )
    db.commit()
    db.refresh(project)
    return PeerReviewedOnlyRead(enabled=project.peer_reviewed_only)


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
    even if another reviewer already has. The queue never carries the AI's suggestion: nobody sees it
    before deciding themselves (X.224), so no reviewer is biased by the AI's (or the other reviewer's)
    answer; `GET /suggestion/{source_id}` shows it once the caller has decided. Two reviewers who agree count as decided;
    disagreement is a `conflict` (M2.4.2b resolves it), not a decision.

    `sort`: `"oldest"` (default), `"relevance"` (M2.6, spec 5.3.2) or `"question_match"` (X.199).
    `"relevance"` ranks by a classifier trained fresh from this project's current human decisions,
    highest predicted relevance first. Only applies when `active_learning_enabled` is on, dual-screen
    is off (ranking would leak the other reviewer's decisions through order) and both decision classes
    exist yet; otherwise falls back to oldest-first.

    `"question_match"` ranks by a lexical (BM25) match of title+abstract against the project's
    research-question text and inclusion-criteria terms, before any human decision exists to learn
    from; each item lists the terms it matched. It never excludes a record — same set and count as
    the default — only the order and the matched terms change. With no research questions and no
    inclusion criteria recorded, it falls back to oldest-first.

    The response's `sort` field says which order was actually used, and `sort_note` explains a
    fallback, never silently different from what was asked without saying so.
    """
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    if sort not in ("oldest", "relevance", "question_match"):
        raise HTTPException(status_code=422, detail="sort must be one of: oldest, relevance, question_match")
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
    sort_note: str | None = None
    matched_terms_by_source: dict[uuid.UUID, tuple[str, ...]] = {}
    if sort == "relevance" and project.active_learning_enabled and not project.dual_screen_enabled:
        classifier = active_learning_classifier(db, project, stage)
        if classifier is not None:
            waiting = active_learning.rank_by_relevance(classifier, waiting, lambda s: f"{s.title or ''} {s.abstract or ''}")
            applied_sort = "relevance"
    elif sort == "question_match":
        query_terms = list(db.scalars(select(ResearchQuestion.text).where(ResearchQuestion.project_id == project.id)).all())
        query_terms += list(
            db.scalars(
                select(ScreeningCriterion.text).where(ScreeningCriterion.project_id == project.id, ScreeningCriterion.kind == "include")
            ).all()
        )
        if query_terms:
            ranked = question_match.rank(waiting, query_terms, lambda s: f"{s.title or ''} {s.abstract or ''}")
            waiting = [source for source, _ in ranked]
            matched_terms_by_source = {source.id: matched for source, matched in ranked}
            applied_sort = "question_match"
        else:
            sort_note = "No research questions or inclusion criteria are recorded for this project yet, so the queue falls back to oldest first."
    items = [
        QueueItem(
            source_id=source.id, title=source.title, authors=source.authors, year=source.year, doi=source.doi,
            abstract=source.abstract, fulltext_access=source.fulltext_access if stage == "full_text" else None,
            found_via=source.found_via, coverage=fulltext_coverage.coverage(source),
            matched_terms=list(matched_terms_by_source.get(source.id, ())), preprint=is_preprint(source),
        )
        for source in waiting[offset : offset + limit]
    ]
    return QueueRead(
        stage=stage, locked=screening_locked(db, project), dual_screen_enabled=project.dual_screen_enabled,
        ai_autoexclude_enabled=project.ai_autoexclude_enabled, sort=applied_sort, sort_note=sort_note,
        counts=counts, pending=len(waiting), peer_reviewed_only=project.peer_reviewed_only,
        preprint_pending=sum(1 for source in waiting if is_preprint(source)), items=items,
    )


class IncludedItem(BaseModel):
    source_id: uuid.UUID
    title: str
    authors: list | None
    year: int | None
    doi: str | None
    abstract: str | None  # untrusted text from a connector: show it as text only
    metadata_verified: bool
    found_via: dict | None = None


class IncludedRead(BaseModel):
    locked: bool  # G3 is approved
    dual_screen_enabled: bool
    passed_title_abstract: int  # settled include or maybe at title/abstract
    awaiting_full_text: int  # of those, no settled full-text decision yet
    items: list[IncludedItem]  # settled full-text includes: the set G3 approves, oldest first


@router.get("/included", response_model=IncludedRead)
def get_included(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """The included set G3 asks a person to approve (X.215): sources with a settled full-text include.

    Read-only. Uses the same settled-decision rule as extraction and the PRISMA flow, so under dual-screen an
    unresolved or one-sided decision is never shown as included and no reviewer's blinded decision is revealed.
    """
    passed = passed_title_abstract(db, project)
    full_text = rows_for_stage(db, project, "full_text")
    awaiting = sum(1 for source_id in passed if settled_decision(project, full_text.get(source_id, [])) is None)
    items = [
        IncludedItem(
            source_id=s.id, title=s.title, authors=s.authors, year=s.year, doi=s.doi, abstract=s.abstract,
            metadata_verified=s.metadata_verified, found_via=s.found_via,
        )
        for s in included_sources(db, project)
    ]
    return IncludedRead(
        locked=screening_locked(db, project), dual_screen_enabled=project.dual_screen_enabled,
        passed_title_abstract=len(passed), awaiting_full_text=awaiting, items=items,
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
    else:
        rows = _without_undisclosed_ai_rows(rows, audit.user_actor(user))
    rows.sort(key=lambda r: (r.decided_at, r.seq), reverse=True)
    return rows[:limit]


def _without_undisclosed_ai_rows(rows: list[ScreeningDecision], viewer: str) -> list[ScreeningDecision]:
    """An AI row is withheld for a source+stage until the viewer has decided it themselves (X.224)."""
    by_key: dict[tuple[uuid.UUID, str], list[ScreeningDecision]] = {}
    for row in rows:
        by_key.setdefault((row.source_id, row.stage), []).append(row)
    kept = []
    for group in by_key.values():
        decided = has_own_decision(group, viewer)
        kept.extend(row for row in group if decided or row.decided_by != "ai")
    return kept


@router.get("/suggestion/{source_id}", response_model=RevealedSuggestion)
def get_revealed_suggestion(
    source_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The AI's title/abstract suggestion for one source, shown only to someone who has decided it themselves.

    Before that the answer is 404, the same as for a source that does not exist (X.224). It is a suggestion
    only: the response says whether it matches the caller's decision and changes nothing.
    """
    source = live_source_or_404(db, project, source_id)
    rows = rows_for_stage(db, project, "title_abstract").get(source.id, [])
    own = human_decisions_by_decider(rows).get(audit.user_actor(user))
    if own is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    suggestion = ai_suggestion(rows)
    shared = db.scalar(
        select(AiSuggestion).where(
            AiSuggestion.project_id == project.id, AiSuggestion.target_kind == "screening", AiSuggestion.target_id == str(source.id)
        ).order_by(AiSuggestion.created_at.desc()).limit(1)
    )
    if suggestion is None and shared is None:
        return RevealedSuggestion(your_decision=own.decision, your_reason_code=own.reason_code, suggestion=None, agrees=None)
    return RevealedSuggestion(
        your_decision=own.decision,
        your_reason_code=own.reason_code,
        agrees=suggestion is not None and suggestion.decision == own.decision and suggestion.reason_code == own.reason_code,
        suggestion=SuggestionRead(
            id=shared.id if shared else None,
            state=suggestions.state_of(event.kind for event in shared.events) if shared else None,
            decision=suggestion.decision if suggestion else "",
            reason_code=suggestion.reason_code if suggestion else None,
            confidence=suggestion.confidence if suggestion else None,
            rationale=suggestion.note if suggestion else None,
            **_stored_evidence(shared),
        ),
    )


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


def _queue_fulltext_fetch_if_finally_included(db: Session, project: Project, source: Source, actor: str) -> Task | None:
    """Queue the fetch only when the source's settled full-text decision is include (human, or arbiter ruling)."""
    final = settled_decision(project, rows_for_stage(db, project, "full_text").get(source.id, []))
    if final is None or final.decision != "include":
        return None
    return _queue_included_fulltext_fetch(db, project, source, actor)


def _queue_included_fulltext_fetch(db: Session, project: Project, source: Source, actor: str) -> Task | None:
    """Queue one licence-checked fetch after a source becomes finally included at full text."""
    if source.fulltext_path:
        return None
    source_ids = source.source_ids if isinstance(source.source_ids, dict) else {}
    settings = get_settings()
    if isinstance(source_ids.get("arxiv"), str) and is_enabled("arxiv", settings.connectors_enabled, settings.connectors_allow_scraping):
        task_type = ARXIV_FULLTEXT_FETCH
    elif source.doi and not unpaywall_unavailable_reason():
        task_type = FULLTEXT_FETCH
    else:
        return None
    open_tasks = db.scalars(
        select(Task).where(Task.project_id == project.id, Task.type.in_((FULLTEXT_FETCH, ARXIV_FULLTEXT_FETCH)))
    )
    for task in open_tasks:
        if task.status in {TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused} and (task.payload or {}).get("source_id") == str(source.id):
            return task
    task = enqueue_task(
        db, project.id, task_type,
        payload={"project_id": str(project.id), "source_id": str(source.id), "requested_by": actor, "automatic": True},
        actor=actor,
        idempotency_key=f"fulltext-auto:{source.id}",
    )
    audit.record(
        db, actor=actor, action="source.fulltext_automatically_requested", project_id=project.id,
        payload={"source_id": str(source.id), "task_id": str(task.id), "via": "arxiv" if task_type == ARXIV_FULLTEXT_FETCH else "doi"},
    )
    return task


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
    problem = check_reason(payload.decision, payload.reason_code, criterion_codes(db, project), payload.note)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
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
    if payload.stage == "full_text":
        _queue_fulltext_fetch_if_finally_included(db, project, source, audit.user_actor(user))
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


class DiscussionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    stage: str = "title_abstract"
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: str) -> str:
        return _check_stage(value)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A discussion note cannot be empty.")
        return value.strip()


class DiscussionNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    stage: str
    author: str
    body: str
    created_at: datetime
    current: bool = True  # belongs to the disagreement as it stands now; False once a reviewer changed their answer


def _discussion_notes(db: Session, source_id: uuid.UUID, stage: str) -> list[ScreeningDiscussionNote]:
    return list(
        db.scalars(
            select(ScreeningDiscussionNote)
            .where(ScreeningDiscussionNote.source_id == source_id, ScreeningDiscussionNote.stage == stage)
            .order_by(ScreeningDiscussionNote.created_at, ScreeningDiscussionNote.id)
        )
    )


@router.post("/discussion", response_model=DiscussionNoteRead, status_code=status.HTTP_201_CREATED)
def add_discussion_note(
    payload: DiscussionIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """A reviewer documents the discussion of their disagreement (X.225). Only someone who currently holds a decision
    on this source, and only while the reviewers disagree and nobody has ruled. Notes are append-only and are what an
    independent arbiter reads before ruling; each note is audited."""
    if screening_locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    if not project.dual_screen_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dual screening is off, so there are no reviewer conflicts to discuss.")
    source = live_source_or_404(db, project, payload.source_id)
    rows = rows_for_stage(db, project, payload.stage).get(source.id, [])
    if dual_screen_status(rows)[0] != "conflict":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a source whose reviewers currently disagree, and that nobody has ruled on, can be discussed.")
    actor = audit.user_actor(user)
    if actor not in human_decisions_by_decider(rows):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only a reviewer who decided this source can take part in its discussion.")
    note = ScreeningDiscussionNote(
        project_id=project.id, source_id=source.id, stage=payload.stage, decision_seq=max(r.seq for r in rows),
        author=actor, body=payload.body,
    )
    db.add(note)
    audit.record(db, actor=actor, action="screening.discussion_noted", project_id=project.id,
                 payload={"source_id": str(source.id), "stage": payload.stage})
    _commit(db)
    db.refresh(note)
    return note


@router.get("/discussion", response_model=list[DiscussionNoteRead])
def list_discussion(
    source_id: uuid.UUID,
    stage: str = "title_abstract",
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The discussion of a conflict, oldest first. Shown only to the reviewers who decided the source and to an owner
    or supervisor who may have to rule; everyone else gets an empty list, so dual-screen blinding holds (X.225)."""
    stage = _check_stage(stage)
    source = live_source_or_404(db, project, source_id)
    rows = rows_for_stage(db, project, stage).get(source.id, [])
    actor = audit.user_actor(user)
    if not (has_taken_part(rows, actor) or project_role(db, project, user) in APPROVE_ROLES):
        return []
    notes = _discussion_notes(db, source.id, stage)
    if dual_screen_status(rows)[0] not in ("conflict", "adjudicated") and not notes:
        return []
    live = {n.id for n in current_discussion(rows, notes)}
    return [DiscussionNoteRead.model_validate(n).model_copy(update={"current": n.id in live}) for n in notes]


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
    afterwards reopens the conflict. Ruling again replaces an earlier ruling (the earlier row stays). The arbiter must
    be independent (no decision of their own, even an undone one, and no discussion note) and must give a written
    rationale; the reviewers must have documented their discussion first (`POST …/discussion`, X.225)."""
    if screening_locked(db, project):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LOCKED_MESSAGE)
    if not project.dual_screen_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dual screening is off, so there are no reviewer conflicts to rule on.")
    source = live_source_or_404(db, project, payload.source_id)
    rows = rows_for_stage(db, project, payload.stage).get(source.id, [])
    if dual_screen_status(rows)[0] not in ("conflict", "adjudicated"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a source whose reviewers disagree can be ruled on.")
    actor = audit.user_actor(user)
    if has_taken_part(rows, actor):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You decided this source yourself, so you can't rule on it.")
    notes = _discussion_notes(db, source.id, payload.stage)
    if any(n.author == actor for n in notes):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You took part in the reviewers' discussion of this source, so you can't rule on it.")
    discussion = current_discussion(rows, notes)
    if not discussion:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The reviewers must discuss this disagreement first. Record the discussion, then rule.")
    if not (payload.note and payload.note.strip()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A ruling needs a written rationale in the note.")
    problem = check_reason(payload.decision, payload.reason_code, criterion_codes(db, project), payload.note)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
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
        payload={"source_id": str(source.id), "stage": payload.stage, "decision": payload.decision, "reason_code": payload.reason_code,
                 "discussion_notes": len(discussion)},
    )
    db.flush()  # the row's id is the ref: each ruling is its own artifact
    register_artifact(db, project.id, SCREENING_KIND, str(row.id), ProjectStage.screened, actor=actor)
    if payload.stage == "full_text":
        _queue_fulltext_fetch_if_finally_included(db, project, source, actor)
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
    note: str | None = None  # a ruling's written rationale (X.225); None for a reviewer's own row


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
    # Aggregate figures: owner only (owner decision 2026-10-19, M2.3.2/M2.3.3). A dual-screen reviewer or
    # supervisor sees `null` here, never the project-wide kappa/counts.
    n: int | None  # items compared (exactly two reviewers)
    agreed: int | None
    percent_agreement: float | None
    kappa: float | None
    band: str | None
    categories: list[str] | None
    matrix: dict[str, dict[str, int]] | None
    note: str | None
    scope: AgreementScope | None
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
    conflict on a record is listed only to a caller who has decided that record too (M2.3.3). The
    aggregate figures (`n`/`agreed`/`kappa`/`band`/`categories`/`matrix`/`scope`) are owner only (owner
    decision 2026-10-19); everyone else gets `null` for those and only the (already blinded) `conflicts`.
    """
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=STAGE_MESSAGE)
    role = project_role(db, project, user)
    # Blinding protects people who can still screen. Someone who cannot screen (a supervisor, a reviewer) is not
    # biased by seeing a conflict, and a supervisor needs to see it to rule on it (M2.4.3; owner decision 2026-10-12).
    can_screen = role in WRITE_ROLES
    is_owner = role == ProjectRole.owner
    result = stage_agreement(db, project, stage, viewer_decider=audit.user_actor(user) if can_screen else None)
    a = result.agreement
    return AgreementRead(
        stage=stage,
        n=a.n if is_owner else None,
        agreed=a.agreed if is_owner else None,
        percent_agreement=a.percent_agreement if is_owner else None,
        kappa=a.kappa if is_owner else None,
        band=a.band if is_owner else None,
        categories=list(a.categories) if is_owner else None,
        matrix=a.matrix if is_owner else None,
        note=a.note if is_owner else None,
        scope=AgreementScope(**result.scope) if is_owner else None,
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
