"""Screening rules (spec 5.3): who is still to screen, what counts as decided, and when it is locked.

A person's decision is final and the latest one wins; an AI decision is only a suggestion and never
makes a source "decided". Decisions are appended, never edited. Once a person has approved gate G3 the
screening is what was approved, so it can't change until G3 is reopened (plan rule 21).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Gate, GateCode, GateStatus, Project, ScreeningCriterion, ScreeningDecision, Source

STAGES = ("title_abstract", "full_text")
HUMAN_DECISIONS = ("include", "exclude", "maybe")
UNDO = "undo"


def screening_locked(db: Session, project: Project) -> bool:
    status = db.scalar(select(Gate.status).where(Gate.project_id == project.id, Gate.code == GateCode.G3))
    return status == GateStatus.approved


def screenable_sources(db: Session, project: Project) -> list[Source]:
    """The sources that take part in screening: not merged into another, oldest first."""
    return list(
        db.scalars(
            select(Source)
            .where(Source.project_id == project.id, Source.merged_into.is_(None))
            .order_by(Source.created_at, Source.id)
        )
    )


def rows_for_stage(db: Session, project: Project, stage: str) -> dict[uuid.UUID, list[ScreeningDecision]]:
    """Every decision of the stage, per source, oldest first."""
    rows = db.scalars(
        select(ScreeningDecision)
        .where(ScreeningDecision.project_id == project.id, ScreeningDecision.stage == stage)
        .order_by(ScreeningDecision.source_id, ScreeningDecision.seq)
    )
    grouped: dict[uuid.UUID, list[ScreeningDecision]] = {}
    for row in rows:
        grouped.setdefault(row.source_id, []).append(row)
    return grouped


def final_decision(rows: list[ScreeningDecision]) -> ScreeningDecision | None:
    """The person's latest decision, or None if there is none or it was undone. AI rows are ignored."""
    human = [r for r in rows if r.decided_by == "human"]
    if not human or human[-1].decision == UNDO:
        return None
    return human[-1]


def ai_suggestion(rows: list[ScreeningDecision]) -> ScreeningDecision | None:
    ai = [r for r in rows if r.decided_by == "ai"]
    return ai[-1] if ai else None


def passed_title_abstract(db: Session, project: Project) -> set[uuid.UUID]:
    """Sources a person included, or marked maybe, at title/abstract: the ones that go on to full text."""
    return {
        source_id
        for source_id, rows in rows_for_stage(db, project, "title_abstract").items()
        if (final := final_decision(rows)) is not None and final.decision in ("include", "maybe")
    }


def criterion_codes(db: Session, project: Project) -> dict[str, str]:
    """The project's reason codes (I1, E2, ...) with their kind."""
    return {c.code: c.kind for c in db.scalars(select(ScreeningCriterion).where(ScreeningCriterion.project_id == project.id))}


def check_reason(decision: str, reason_code: str | None, codes: dict[str, str]) -> str | None:
    """Why a decision's reason code is not acceptable, or None. Exclusions must say which exclusion criterion applies."""
    if reason_code is None:
        if decision == "exclude":
            return "An exclusion needs a reason code: one of the project's exclusion criteria (E1, E2, ...)."
        return None
    kind = codes.get(reason_code)
    if kind is None:
        return f"'{reason_code}' is not one of this project's criteria. Add it to the criteria first."
    if decision == "exclude" and kind != "exclude":
        return "An exclusion must cite an exclusion criterion (E1, E2, ...)."
    if decision == "include" and kind != "include":
        return "An inclusion must cite an inclusion criterion (I1, I2, ...)."
    return None


def next_seq(db: Session, source_id: uuid.UUID, stage: str) -> int:
    """The next row number for this source and stage (the unique index makes a clash fail loudly)."""
    last = db.scalar(
        select(ScreeningDecision.seq)
        .where(ScreeningDecision.source_id == source_id, ScreeningDecision.stage == stage)
        .order_by(ScreeningDecision.seq.desc())
        .limit(1)
    )
    return (last or 0) + 1
