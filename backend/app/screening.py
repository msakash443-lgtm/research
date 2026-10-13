"""Screening rules (spec 5.3): who is still to screen, what counts as decided, and when it is locked.

A person's decision is final and the latest one wins; an AI decision is only a suggestion and never
makes a source "decided". Decisions are appended, never edited. Once a person has approved gate G3 the
screening is what was approved, so it can't change until G3 is reopened (plan rule 21).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import active_learning, ai_quality
from app.agreement import Agreement, cohen_kappa
from app.models import AutoExcludeAudit, Gate, GateCode, GateStatus, Project, ScreeningCriterion, ScreeningDecision, Source

STAGES = ("title_abstract", "full_text")
HUMAN_DECISIONS = ("include", "exclude", "maybe")
UNDO = "undo"

ARBITER = "arbiter"  # `decided_by` of an owner's or supervisor's ruling on a reviewer conflict (M2.4.3)


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


def settled_decision(project: Project, rows: list[ScreeningDecision]) -> ScreeningDecision | None:
    """The decision that counts for one source at one stage: the person's latest decision in single-reviewer
    mode, or the agreed (or adjudicated) decision under dual-screen. None while unscreened, awaiting a second
    reviewer or in conflict, so those never count as included or excluded (PRISMA flow, exports)."""
    if project.dual_screen_enabled:
        status, representative = dual_screen_status(rows)
        return representative if status in ("agreed", "adjudicated") else None
    return final_decision(rows)


def ai_suggestion(rows: list[ScreeningDecision]) -> ScreeningDecision | None:
    ai = [r for r in rows if r.decided_by == "ai"]
    return ai[-1] if ai else None


def human_decisions_by_decider(rows: list[ScreeningDecision]) -> dict[str, ScreeningDecision]:
    """Each human decider's latest decision, keyed by `decider`. A decider whose latest action was
    `undo` is absent (they currently have nothing decided), independently of what any other decider did."""
    latest: dict[str, ScreeningDecision] = {}
    for row in rows:
        if row.decided_by == "human":
            latest[row.decider] = row
    return {decider: row for decider, row in latest.items() if row.decision != UNDO}


def adjudication(rows: list[ScreeningDecision]) -> ScreeningDecision | None:
    """The arbiter's ruling that currently settles a conflict, or None. The latest arbiter row wins; it stands only
    while no reviewer has acted since it (a later reviewer decision or undo reopens the question). Arbiter rows
    never count as a reviewer's decision, so agreement/kappa still compare the two reviewers' own answers."""
    arbiter = [r for r in rows if r.decided_by == ARBITER]
    if not arbiter:
        return None
    last = arbiter[-1]
    if any(r.decided_by == "human" and r.seq > last.seq for r in rows):
        return None
    return last


def dual_screen_status(rows: list[ScreeningDecision]) -> tuple[str, ScreeningDecision | None]:
    """Dual-screen status of one source+stage: `"unscreened"` (nobody has decided), `"awaiting_second"`
    (exactly one independent reviewer has), `"agreed"` (two or more agree — the representative row is
    returned), `"adjudicated"` (they disagree and an owner or supervisor has ruled — the ruling is returned)
    or `"conflict"` (two or more disagree and nobody has ruled; nobody's answer is the final one)."""
    current = human_decisions_by_decider(rows)
    if not current:
        return "unscreened", None
    if len(current) == 1:
        return "awaiting_second", None
    decisions = {row.decision for row in current.values()}
    if len(decisions) == 1:
        return "agreed", next(iter(current.values()))
    ruling = adjudication(rows)
    if ruling is not None:
        return "adjudicated", ruling
    return "conflict", None


def visible_rows(rows: list[ScreeningDecision], viewer_decider: str) -> list[ScreeningDecision]:
    """Dual-screen blinding: hide another decider's row for a source+stage until the viewer has their
    own current (non-undone) decision there too. Call only when the project has dual-screen enabled."""
    by_key: dict[tuple[uuid.UUID, str], list[ScreeningDecision]] = {}
    for row in rows:
        by_key.setdefault((row.source_id, row.stage), []).append(row)
    visible: list[ScreeningDecision] = []
    for grp in by_key.values():
        i_decided = viewer_decider in human_decisions_by_decider(grp)
        for row in grp:
            if i_decided or row.decider == viewer_decider:
                visible.append(row)
    return visible


def has_any_human_decision(rows: list[ScreeningDecision]) -> bool:
    """At least one decider currently has a non-undone human decision (dual-screen-aware)."""
    return bool(human_decisions_by_decider(rows))


def passed_title_abstract(db: Session, project: Project) -> set[uuid.UUID]:
    """Sources a person included, or marked maybe, at title/abstract: the ones that go on to full text."""
    passed: set[uuid.UUID] = set()
    for source_id, rows in rows_for_stage(db, project, "title_abstract").items():
        if project.dual_screen_enabled:
            status, rep = dual_screen_status(rows)
            if status in ("agreed", "adjudicated") and rep.decision in ("include", "maybe"):
                passed.add(source_id)
        elif (final := final_decision(rows)) is not None and final.decision in ("include", "maybe"):
            passed.add(source_id)
    return passed


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


def autoexclude_threshold(project: Project, default: float) -> float:
    """The confidence threshold auto-exclude uses for this project: its own override, else `default`
    (`settings.screening_ai_autoexclude_confidence_threshold`)."""
    return project.ai_autoexclude_confidence_threshold if project.ai_autoexclude_confidence_threshold is not None else default


def audit_statuses_by_decision(db: Session, project: Project) -> dict[uuid.UUID, str]:
    """Every auto-exclude audit row of this project, keyed by the `ScreeningDecision.id` it audits."""
    rows = db.scalars(select(AutoExcludeAudit).where(AutoExcludeAudit.project_id == project.id))
    return {row.decision_id: row.status for row in rows}


def effective_ai_exclusion(
    rows: list[ScreeningDecision], audit_statuses: dict[uuid.UUID, str], *, enabled: bool, threshold: float,
    prescreen_disabled: bool = False,
) -> ScreeningDecision | None:
    """The AI's exclude row, if the auto-exclude policy (M2.5.1, spec 5.3 human gate) currently treats
    it as the final decision: confidence >= threshold, and either never sampled for the human audit or
    sampled and confirmed. Call only when no human has decided the source (`final_decision` is None) —
    a person's own decision always wins, regardless of what the AI suggested.
    `prescreen_disabled` (M2.5.2): once the agreement guard has tripped, an unsampled exclude stops
    counting and goes back to a person; a sampled-and-confirmed exclude still counts."""
    ai = ai_suggestion(rows)
    if ai is None or not enabled:
        return None
    eligible = ai_quality.is_autoexclude_eligible(ai.decision, ai.confidence, threshold)
    status = ai_quality.autoexclude_status(eligible=eligible, audit_status=audit_statuses.get(ai.id))
    if status == ai_quality.TRUSTED and prescreen_disabled:
        return None
    return ai if ai_quality.counts_as_excluded(status) else None


def ai_human_pairs(db: Session, project: Project, stage: str) -> list[tuple[str, str]]:
    """(AI suggestion, person's own eventual decision) for every source where both exist at this
    stage (spec 5.3 quality checks): how often the AI's first guess matches what a person decided.
    Human-vs-human disagreement under dual-screen is a separate concern (M2.4.2b); this only needs
    `final_decision`'s latest-human-row reading, the same one single-reviewer mode always used."""
    grouped = rows_for_stage(db, project, stage)
    pairs: list[tuple[str, str]] = []
    for rows in grouped.values():
        ai, human = ai_suggestion(rows), final_decision(rows)
        if ai is not None and human is not None:
            pairs.append((ai.decision, human.decision))
    return pairs


def ai_human_agreement(db: Session, project: Project, stage: str):
    """Cohen's kappa between the AI's suggestion and the person's own eventual decision at this
    stage (spec 5.3 quality checks). Returns `app.agreement.Agreement`."""
    return cohen_kappa(ai_human_pairs(db, project, stage))


def active_learning_training_texts(db: Session, project: Project, stage: str) -> tuple[list[str], list[str]]:
    """(positive texts, negative texts) for active-learning prioritisation (M2.6): every source's
    title+abstract at this stage a person has decided — include/maybe is positive, exclude negative."""
    grouped = rows_for_stage(db, project, stage)
    sources_by_id = {s.id: s for s in screenable_sources(db, project)}
    positive: list[str] = []
    negative: list[str] = []
    for source_id, rows in grouped.items():
        source = sources_by_id.get(source_id)
        decision = final_decision(rows)
        if source is None or decision is None:
            continue
        text = f"{source.title or ''} {source.abstract or ''}"
        (positive if decision.decision in ("include", "maybe") else negative).append(text)
    return positive, negative


def active_learning_classifier(db: Session, project: Project, stage: str) -> active_learning.Classifier | None:
    """A fresh classifier trained on this project's current human decisions, or None if either class
    (include/maybe, exclude) has no examples yet — too early to predict a direction."""
    positive, negative = active_learning_training_texts(db, project, stage)
    return active_learning.train(positive, negative)


@dataclass(frozen=True)
class StageAgreement:
    agreement: Agreement
    scope: dict[str, int]  # stage_sources, no_reviewer, one_reviewer, two_reviewers, three_or_more_reviewers
    conflicts: list[dict]  # source_id, title, decisions: [{decider, decision, reason_code, decided_at}], oldest decider first


def stage_sources(db: Session, project: Project, stage: str) -> list[Source]:
    sources = screenable_sources(db, project)
    if stage == "full_text":
        passed = passed_title_abstract(db, project)
        sources = [s for s in sources if s.id in passed]
    return sources


def stage_agreement(db: Session, project: Project, stage: str, viewer_decider: str | None = None) -> StageAgreement:
    """Inter-rater agreement for one stage (spec 5.3.3). Compares every source with decisions from exactly two
    distinct human reviewers; sources with no or one reviewer, or more than two, are counted in `scope` only.
    Any source whose current reviewers disagree is listed in `conflicts`, including 3+-reviewer disagreements.
    In dual-screen mode, when `viewer_decider` is given (a screener's own view), a source counts toward every figure
    here only if that viewer has decided it too: the same blinding rule as `visible_rows`. Otherwise the counts,
    the matrix and kappa would reveal other reviewers' decisions on records the viewer has not decided."""
    grouped = rows_for_stage(db, project, stage)
    pairs: list[tuple[str, str]] = []
    conflicts: list[dict] = []
    no_reviewer = one_reviewer = three_or_more = 0
    for source in stage_sources(db, project, stage):
        by_decider = human_decisions_by_decider(grouped.get(source.id, []))
        if project.dual_screen_enabled and viewer_decider is not None and viewer_decider not in by_decider:
            continue  # blinded: not decided by this screener, so it is not counted at all
        ordered = sorted(by_decider.items())  # deterministic reviewer order
        if len(ordered) == 0:
            no_reviewer += 1
            continue
        if len(ordered) == 1:
            one_reviewer += 1
            continue
        if len(ordered) == 2:
            pairs.append((ordered[0][1].decision, ordered[1][1].decision))
        else:
            three_or_more += 1
        if len({row.decision for _, row in ordered}) > 1:
            ruling = adjudication(grouped.get(source.id, []))
            conflicts.append({
                "source_id": source.id,
                "title": source.title,
                "decisions": [
                    {"decider": decider, "decision": row.decision, "reason_code": row.reason_code, "decided_at": row.decided_at}
                    for decider, row in ordered
                ],
                "adjudication": None if ruling is None else {
                    "decider": ruling.decider, "decision": ruling.decision, "reason_code": ruling.reason_code, "decided_at": ruling.decided_at,
                },
            })
    two = len(pairs)
    scope = {
        "stage_sources": no_reviewer + one_reviewer + two + three_or_more,
        "no_reviewer": no_reviewer, "one_reviewer": one_reviewer, "two_reviewers": two, "three_or_more_reviewers": three_or_more,
    }
    return StageAgreement(agreement=cohen_kappa(pairs), scope=scope, conflicts=conflicts)
