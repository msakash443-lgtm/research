"""Screening AI pre-screen results as shared AI suggestions (plan X.46.6).

The pre-screen still writes its `ScreeningDecision(decided_by="ai")` row (agreement statistics and auto-exclude
read it). Next to it, `store_prescreen_suggestion` stores one `AiSuggestion` (target kind `screening`, target id =
the source id) so the screen can show it through the shared panel. What a person does with it:

* A person sees the suggestion only after their own decision on that source (X.224), in single and dual-screen
  projects alike: before that the queue, the suggestion endpoints and the decision history all withhold it.
* Accept / Edit then accept: replaces THEIR OWN decision with the suggested decision and reason code ("change my
  decision to the suggestion"), as if they had pressed the button themselves: same checks, same audit event, same
  artifact. Edited text becomes the note. Refused while they have no decision of their own.
* Undo: appends a human "undo" row, exactly like the Undo key. Nothing is deleted.
* Discard / Restore only change the suggestion's state; they never write a decision.

The suggestion endpoints hide this kind from anyone without their own decision (`visible_to`, used by
`app.routers.suggestions`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, suggestion_store
from app.artifacts import register_artifact
from app.models import AiSuggestion, Project, ProjectStage, ScreeningDecision, Source
from app.screening import (
    UNDO,
    check_reason,
    criterion_codes,
    final_decision,
    has_any_human_decision,
    has_own_decision,
    human_decisions_by_decider,
    next_seq,
    rows_for_stage,
    screening_locked,
)

TARGET_KIND = "screening"
STAGE = "title_abstract"
SCREENING_ARTIFACT_KIND = "screening_decision"  # same artifact kind the decisions endpoint registers (X.42)
LOCKED_MESSAGE = "Gate G3 is approved for this screening. Reopen the project at the screening stage to change a decision."


def decided_target_ids(db: Session, project: Project, actor: str, target_ids: list[str]) -> set[str]:
    """Which of these source ids (as the suggestions store them) the actor has personally decided at the stage."""
    ids = []
    for target_id in target_ids:
        try:
            ids.append(uuid.UUID(target_id))
        except ValueError:
            continue
    if not ids:
        return set()
    by_source: dict[uuid.UUID, list[ScreeningDecision]] = {}
    for row in db.scalars(
        select(ScreeningDecision)
        .where(ScreeningDecision.project_id == project.id, ScreeningDecision.stage == STAGE, ScreeningDecision.source_id.in_(ids))
        .order_by(ScreeningDecision.seq)
    ):
        by_source.setdefault(row.source_id, []).append(row)
    return {str(source_id) for source_id, rows in by_source.items() if has_own_decision(rows, actor)}


def visible_to(db: Session, project: Project, actor: str, rows: list[AiSuggestion]) -> list[AiSuggestion]:
    """The suggestions this person may see: every non-screening one, and a screening one only for a source they
    have decided themselves (X.224)."""
    screening = [row.target_id for row in rows if row.target_kind == TARGET_KIND]
    decided = decided_target_ids(db, project, actor, screening) if screening else set()
    return [row for row in rows if row.target_kind != TARGET_KIND or row.target_id in decided]


def suggestion_text(decision: str, reason_code: str | None, rationale: str) -> str:
    head = decision.capitalize() + (f" ({reason_code})" if reason_code else "")
    return f"{head}: {rationale}"


def store_prescreen_suggestion(
    db: Session, *, project: Project, source: Source, decision: str, reason_code: str | None, confidence: float,
    rationale: str, model_id: str, prompt_version: str, producing_agent: str,
    evidence_quote: str | None = None, evidence_source: str | None = None,
) -> AiSuggestion:
    return suggestion_store.create_suggestion(
        db,
        project_id=project.id,
        target_kind=TARGET_KIND,
        target_id=str(source.id),
        content=suggestion_text(decision, reason_code, rationale),
        confidence=confidence,
        # Item 0 is read by `_apply` (decision, reason); the quote, when the model gave one, is item 1 (X.77).
        evidence=[{"decision": decision, "reason_code": reason_code},
                  *([{"source": evidence_source, "quote": evidence_quote}] if evidence_quote else [])],
        model_id=model_id,
        prompt_version=prompt_version,
        producing_agent=producing_agent,
    )


def _source_and_project(db: Session, suggestion: AiSuggestion) -> tuple[Project, Source]:
    project = db.get(Project, suggestion.project_id)
    source = db.get(Source, uuid.UUID(suggestion.target_id))
    if project is None or source is None or source.project_id != project.id or source.merged_into is not None:
        raise ValueError("The record this suggestion is about no longer exists.")
    return project, source


def _apply(db: Session, suggestion: AiSuggestion, text: str, actor: str) -> str | None:
    project, source = _source_and_project(db, suggestion)
    if screening_locked(db, project):
        raise ValueError(LOCKED_MESSAGE)
    evidence = (suggestion.evidence_json or [{}])[0]
    decision, reason_code = evidence.get("decision"), evidence.get("reason_code")
    problem = check_reason(decision, reason_code, criterion_codes(db, project))
    if problem:
        raise ValueError(problem)
    rows = rows_for_stage(db, project, STAGE).get(source.id, [])
    own = human_decisions_by_decider(rows).get(actor)
    if own is None:
        raise ValueError("Decide this record yourself first. The AI suggestion is shown only after your own decision.")
    if own.decision == decision and own.reason_code == reason_code:
        raise ValueError("Your decision already matches this suggestion.")
    if decision == "exclude" and has_any_human_decision(rows_for_stage(db, project, "full_text").get(source.id, [])):
        raise ValueError("This source already has a full-text decision. Undo that first.")
    note = None if text == suggestion.content else text.strip()[:2000] or None
    row = ScreeningDecision(
        project_id=project.id, source_id=source.id, stage=STAGE, seq=next_seq(db, source.id, STAGE), decision=decision,
        reason_code=reason_code, decided_by="human", decider=actor, note=note,
    )
    db.add(row)
    audit.record(
        db, actor=actor, action="screening.decided", project_id=project.id,
        payload={"source_id": str(source.id), "stage": STAGE, "decision": decision, "reason_code": reason_code,
                 "decided_by": "human", "from_suggestion_id": str(suggestion.id)},
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ValueError("Someone else just decided this source. Reload and try again.") from None
    register_artifact(db, project.id, SCREENING_ARTIFACT_KIND, str(row.id), ProjectStage.screened, actor=actor)
    return str(row.id)


def _revert(db: Session, suggestion: AiSuggestion, result_ref: str | None, actor: str) -> str | None:
    project, source = _source_and_project(db, suggestion)
    if screening_locked(db, project):
        raise ValueError(LOCKED_MESSAGE)
    rows = rows_for_stage(db, project, STAGE).get(source.id, [])
    latest = final_decision(rows)
    if result_ref is None or latest is None or str(latest.id) != result_ref:
        raise ValueError("The decision made from this suggestion has since changed, so it cannot be undone from here.")
    row = ScreeningDecision(
        project_id=project.id, source_id=source.id, stage=STAGE, seq=next_seq(db, source.id, STAGE), decision=UNDO,
        reason_code=None, decided_by="human", decider=actor,
    )
    db.add(row)
    audit.record(
        db, actor=actor, action="screening.undone", project_id=project.id,
        payload={"source_id": str(source.id), "stage": STAGE, "decided_by": "human", "from_suggestion_id": str(suggestion.id)},
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ValueError("Someone else just decided this source. Reload and try again.") from None
    return str(row.id)


suggestion_store.TARGET_HANDLERS[TARGET_KIND] = suggestion_store.TargetHandler(_apply, _revert)
