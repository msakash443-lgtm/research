"""Screening AI pre-screen results as shared AI suggestions (plan X.46.6).

The pre-screen still writes its `ScreeningDecision(decided_by="ai")` row (agreement statistics and auto-exclude
read it). Next to it, `store_prescreen_suggestion` stores one `AiSuggestion` (target kind `screening`, target id =
the source id) so the screen can show it through the shared panel. What a person does with it:

* Accept / Edit then accept: records THEIR OWN human decision (the suggested decision and reason code), as if they
  had pressed the button themselves: same checks, same audit event, same artifact. Edited text becomes the note.
* Undo: appends a human "undo" row, exactly like the Undo key. Nothing is deleted.
* Discard / Restore only change the suggestion's state; they never write a decision.

Dual screening: the screen withholds AI suggestions until a reviewer has decided (M2.4.1), so accepting is refused
there and the suggestion list hides this kind (`app.routers.suggestions`).
"""

from __future__ import annotations

import uuid

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
    next_seq,
    rows_for_stage,
    screening_locked,
)

TARGET_KIND = "screening"
STAGE = "title_abstract"
SCREENING_ARTIFACT_KIND = "screening_decision"  # same artifact kind the decisions endpoint registers (X.42)
LOCKED_MESSAGE = "Gate G3 is approved for this screening. Reopen the project at the screening stage to change a decision."


def suggestion_text(decision: str, reason_code: str | None, rationale: str) -> str:
    head = decision.capitalize() + (f" ({reason_code})" if reason_code else "")
    return f"{head}: {rationale}"


def store_prescreen_suggestion(
    db: Session, *, project: Project, source: Source, decision: str, reason_code: str | None, confidence: float,
    rationale: str, model_id: str, prompt_version: str, producing_agent: str,
) -> AiSuggestion:
    return suggestion_store.create_suggestion(
        db,
        project_id=project.id,
        target_kind=TARGET_KIND,
        target_id=str(source.id),
        content=suggestion_text(decision, reason_code, rationale),
        confidence=confidence,
        evidence=[{"decision": decision, "reason_code": reason_code}],
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
    if project.dual_screen_enabled:
        raise ValueError("Dual screening is on: AI suggestions are not shown or accepted there. Decide the record yourself.")
    if screening_locked(db, project):
        raise ValueError(LOCKED_MESSAGE)
    evidence = (suggestion.evidence_json or [{}])[0]
    decision, reason_code = evidence.get("decision"), evidence.get("reason_code")
    problem = check_reason(decision, reason_code, criterion_codes(db, project))
    if problem:
        raise ValueError(problem)
    rows = rows_for_stage(db, project, STAGE).get(source.id, [])
    if final_decision(rows) is not None:
        raise ValueError("You have already decided this record. Undo that decision first.")
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
