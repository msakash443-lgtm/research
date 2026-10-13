"""Machine-checkable exit conditions per stage transition (docs/stage-exit-conditions.md, X.48.1).

A condition belongs to a transition (leave stage A, enter stage B). `conditions(db, project, target)`
returns the conditions that apply to moving the project from its current stage to `target`.

- Built rows run a real check on stored data.
- Rows that need a task not yet built have no stored data. They return Information with
  `met=False` and "not built yet". They never report `met=True`.
- EC-0.1 (the target's gate is approved) is the check each gate row EC-x.1 runs on its own
  transition, so it is not listed a second time.

Nothing here blocks anything. X.48.3 makes advance and gate approval refuse on Required rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import criteria as criteria_rules
from app import prisma, recall_report, screening, stage_order
from app.artifacts import gate_for_stage, unreplaced_stale
from app.gates import ensure_gates, gate_is_approved, pending_earlier_gates
from app.models import (
    SOURCE_ORIGIN_RETRIEVED,
    AutoExcludeAudit,
    ClusterRun,
    ContextKind,
    Extraction,
    GateCode,
    Project,
    ProjectStage,
    ResearchContextItem,
    ResearchRun,
    ResearchRunStatus,
    ScreeningCriterion,
    SeedPaper,
    Source,
)


class Level(str, Enum):
    required = "required"
    recommended = "recommended"
    information = "information"


@dataclass(frozen=True)
class Condition:
    id: str
    level: Level
    met: bool
    message: str
    fix: str | None


@dataclass(frozen=True)
class Rule:
    id: str
    frm: ProjectStage | None  # the leaving stage; None = every stage
    to: ProjectStage | None  # the entering stage; None = every stage
    check: Callable[[Session, Project], Condition]


def _count(db: Session, statement) -> int:
    return db.scalar(statement.with_only_columns(func.count()).order_by(None)) or 0


def _gate(db: Session, project: Project, code: GateCode, rule_id: str, fix: str) -> Condition:
    """EC-0.1 for one gate: a person has approved it."""
    met = gate_is_approved(db, project.id, code)
    return Condition(
        rule_id,
        Level.required,
        met,
        f"{code.value} approved" if met else f"{code.value} is not approved yet",
        None if met else fix,
    )


def _not_built(rule_id: str, task: str) -> Condition:
    return Condition(rule_id, Level.information, False, f"not built yet (needs {task})", None)


# --- Global rules, evaluated for every target ------------------------------------------------


def _ec_0_2(db: Session, project: Project, target: ProjectStage) -> Condition:
    stale = unreplaced_stale(db, project, up_to=target)
    met = not stale
    return Condition(
        "EC-0.2",
        Level.required,
        met,
        f"no stale results at or before {target.value}"
        if met
        else f"{len(stale)} stale result(s) at or before {target.value}",
        None if met else "Redo list: open each stale item on its own screen",
    )


def _ec_0_3(db: Session, project: Project, target: ProjectStage) -> Condition | None:
    code = gate_for_stage(target)
    if code is None:
        return None
    pending = pending_earlier_gates(ensure_gates(db, project), code)
    met = not pending
    return Condition(
        "EC-0.3",
        Level.required,
        met,
        "earlier gates are approved" if met else "earlier gates not approved: " + ", ".join(c.value for c in pending),
        None if met else f"Approve {pending[0].value} first",
    )


def _ec_0_4(db: Session, project: Project, target: ProjectStage) -> Condition:
    met = target in stage_order.chain(project)
    return Condition(
        "EC-0.4",
        Level.required,
        met,
        f"{target.value} is on this project's chain" if met else f"{target.value} is not on this project's chain",
        None,
    )


# --- idea -> scoped --------------------------------------------------------------------------


def ec_1_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G1, "EC-1.1", "Scope: approve G1")


def ec_1_2(db: Session, project: Project) -> Condition:
    count = _count(
        db,
        select(ResearchContextItem).where(
            ResearchContextItem.project_id == project.id,
            ResearchContextItem.kind.in_([ContextKind.question, ContextKind.objective]),
        ),
    )
    met = count >= 1
    return Condition(
        "EC-1.2",
        Level.recommended,
        met,
        "a research question or objective is written" if met else "no research question or objective written",
        None if met else 'Scope: "Add question"',
    )


def ec_1_3(db: Session, project: Project) -> Condition:
    count = _count(
        db,
        select(ResearchContextItem).where(
            ResearchContextItem.project_id == project.id, ResearchContextItem.kind == ContextKind.idea
        ),
    )
    met = count == 0
    return Condition(
        "EC-1.3",
        Level.information,
        met,
        "no captured ideas left unpromoted" if met else f"{count} captured idea(s) not yet promoted",
        None if met else "Scope: show ideas",
    )


# --- scoped -> search_planned ----------------------------------------------------------------


def ec_2_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G2, "EC-2.1", "Criteria: approve G2")


def ec_2_2(db: Session, project: Project) -> Condition:
    criteria = list(db.scalars(select(ScreeningCriterion).where(ScreeningCriterion.project_id == project.id)))
    problems = criteria_rules.problems(project.criteria_framework, criteria)
    met = not problems
    return Condition(
        "EC-2.2",
        Level.recommended,
        met,
        "criteria framework chosen and complete" if met else " ".join(problems),
        None if met else "Criteria: edit criteria",
    )


def ec_2_3(db: Session, project: Project) -> Condition:
    count = _count(db, select(SeedPaper).where(SeedPaper.project_id == project.id))
    met = count >= 1
    return Condition(
        "EC-2.3",
        Level.information,
        met,
        f"{count} seed paper(s) recorded" if met else "no seed papers recorded for the recall check",
        None if met else 'Criteria & seeds: "Add seed paper"',
    )


# --- search_planned -> retrieved -------------------------------------------------------------


def ec_3_1(db: Session, project: Project) -> Condition:
    met = bool(prisma.latest_runs(db, project))
    return Condition(
        "EC-3.1",
        Level.recommended,
        met,
        "a search has run and reported counts" if met else "no search has run yet",
        None if met else 'Search: "New search"',
    )


def ec_3_2(db: Session, project: Project) -> Condition:
    if _count(db, select(SeedPaper).where(SeedPaper.project_id == project.id)) == 0:
        return Condition("EC-3.2", Level.recommended, True, "no seed papers to check against", None)
    met = recall_report.project_report(db, project)["meets_target"] is True
    return Condition(
        "EC-3.2",
        Level.recommended,
        met,
        "every seed paper was found by the searches" if met else "some seed papers were not found by the searches",
        None if met else "Results check: open the missed-papers list",
    )


def ec_3_3(db: Session, project: Project) -> Condition:
    count = _count(
        db,
        select(Source).where(
            Source.project_id == project.id,
            Source.origin == SOURCE_ORIGIN_RETRIEVED,
            Source.metadata_verified.is_(False),
        ),
    )
    met = count == 0
    return Condition(
        "EC-3.3",
        Level.information,
        met,
        "all auto-retrieved sources are verified" if met else f"{count} auto-retrieved source(s) not yet verified",
        None if met else 'Sources: filter "unverified"',
    )


# --- retrieved -> screened -------------------------------------------------------------------


def ec_4_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G3, "EC-4.1", "Screening: approve G3")


def ec_4_2(db: Session, project: Project) -> Condition:
    if not project.dual_screen_enabled:
        return Condition("EC-4.2", Level.required, True, "dual-screen is off", None)
    sources = screening.screenable_sources(db, project)
    conflicts = 0
    for stage in screening.STAGES:
        rows_by_source = screening.rows_for_stage(db, project, stage)
        conflicts += sum(
            1
            for source in sources
            if screening.dual_screen_status(rows_by_source.get(source.id, []))[0] == "conflict"
        )
    met = conflicts == 0
    return Condition(
        "EC-4.2",
        Level.required,
        met,
        "no unresolved screening conflicts" if met else f"{conflicts} screening conflict(s) unresolved",
        None if met else "Screening: conflict queue",
    )


def ec_4_3(db: Session, project: Project) -> Condition:
    rows_by_source = screening.rows_for_stage(db, project, "title_abstract")
    unscreened = sum(
        1
        for source in screening.screenable_sources(db, project)
        if screening.settled_decision(project, rows_by_source.get(source.id, [])) is None
    )
    met = unscreened == 0
    return Condition(
        "EC-4.3",
        Level.recommended,
        met,
        "every record is screened at title/abstract" if met else f"{unscreened} records unscreened",
        None if met else 'Screening: queue filtered "unscreened"',
    )


def ec_4_4(db: Session, project: Project) -> Condition:
    passed = screening.passed_title_abstract(db, project)
    full_text = screening.rows_for_stage(db, project, "full_text")
    missing = sum(1 for source_id in passed if screening.settled_decision(project, full_text.get(source_id, [])) is None)
    met = missing == 0
    return Condition(
        "EC-4.4",
        Level.recommended,
        met,
        "every record that passed title/abstract has a full-text decision"
        if met
        else f"{missing} record(s) passed title/abstract but have no full-text decision",
        None if met else "Screening: full-text queue",
    )


def ec_4_5(db: Session, project: Project) -> Condition:
    pending = _count(
        db,
        select(AutoExcludeAudit).where(AutoExcludeAudit.project_id == project.id, AutoExcludeAudit.status == "pending"),
    )
    met = pending == 0
    return Condition(
        "EC-4.5",
        Level.recommended,
        met,
        "every sampled AI auto-exclude is audited" if met else f"{pending} sampled AI auto-exclude(s) not yet audited",
        None if met else "Screening: audit sample",
    )


def ec_4_6(db: Session, project: Project) -> Condition:
    excluded = 0
    without_reason = 0
    for stage in screening.STAGES:
        for rows in screening.rows_for_stage(db, project, stage).values():
            decision = screening.settled_decision(project, rows)
            if decision is not None and decision.decision == "exclude":
                excluded += 1
                if decision.reason_code is None:
                    without_reason += 1
    met = without_reason == 0
    return Condition(
        "EC-4.6",
        Level.information,
        met,
        f"{excluded} excluded record(s), each with a reason code"
        if met
        else f"{without_reason} excluded record(s) without a reason code",
        None,
    )


def ec_4_7(db: Session, project: Project) -> Condition:
    met = prisma.flow_counts(db, project)["screening"] is not None
    return Condition(
        "EC-4.7",
        Level.recommended,
        met,
        "PRISMA screening numbers are computed from stored decisions" if met else "PRISMA screening numbers are not available",
        None if met else "Results check: PRISMA",
    )


# --- screened -> extracted -------------------------------------------------------------------


def ec_5_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G4, "EC-5.1", "Extraction review: approve G4")


def ec_5_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-5.2", "M3.4")


def ec_5_3(db: Session, project: Project) -> Condition:
    return _not_built("EC-5.3", "M3.6")


def ec_5_4(db: Session, project: Project) -> Condition:
    rows_by_source = screening.rows_for_stage(db, project, "full_text")
    included = [
        source.id
        for source in screening.screenable_sources(db, project)
        if (decision := screening.settled_decision(project, rows_by_source.get(source.id, []))) is not None
        and decision.decision == "include"
    ]
    extracted = set(db.scalars(select(Extraction.source_id).where(Extraction.project_id == project.id)))
    missing = sum(1 for source_id in included if source_id not in extracted)
    met = missing == 0
    return Condition(
        "EC-5.4",
        Level.recommended,
        met,
        "every included study has an extraction" if met else f"{missing} included stud(ies) without an extraction",
        None if met else 'Extraction: "Start extraction"',
    )


def ec_5_5(db: Session, project: Project) -> Condition:
    return _not_built("EC-5.5", "M3.3")


# --- extracted -> synthesized ----------------------------------------------------------------


def ec_6_1(db: Session, project: Project) -> Condition:
    completed = _count(
        db,
        select(ResearchRun).where(
            ResearchRun.project_id == project.id, ResearchRun.status == ResearchRunStatus.completed
        ),
    )
    clusters = _count(db, select(ClusterRun).where(ClusterRun.project_id == project.id))
    met = completed + clusters >= 1
    return Condition(
        "EC-6.1",
        Level.recommended,
        met,
        "a synthesis result exists" if met else "no synthesis result yet",
        None if met else "Ask / Themes",
    )


def ec_6_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-6.2", "M3.11")


def ec_6_3(db: Session, project: Project) -> Condition:
    return _not_built("EC-6.3", "M3.14")


def ec_6_4(db: Session, project: Project) -> Condition:
    return _not_built("EC-6.4", "X.46")


# --- synthesized and later: gates and not-built rows -----------------------------------------


def ec_7_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G5, "EC-7.1", "Gaps: approve G5")


def ec_7_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-7.2", "M3.9")


def ec_8_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G6, "EC-8.1", "Approvals: approve G6")


def ec_8_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-8.2", "M4.1")


def ec_9_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G7, "EC-9.1", "Approvals: approve G7")


def ec_10_1(db: Session, project: Project) -> Condition:
    return _not_built("EC-10.1", "M5.1, Q4")


def ec_11_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G8, "EC-11.1", "Approvals: approve G8")


def ec_11_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-11.2", "M5.1")


def ec_12_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G9, "EC-12.1", "Approvals: approve G9")


def ec_12_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-12.2", "M5.3")


def ec_13_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G10, "EC-13.1", "Approvals: approve G10")


def ec_13_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-13.2", "M6.3")


def ec_13_3(db: Session, project: Project) -> Condition:
    return _not_built("EC-13.3", "M6.2")


def ec_14_1(db: Session, project: Project) -> Condition:
    return _not_built("EC-14.1", "M7.1")


def ec_15_1(db: Session, project: Project) -> Condition:
    return _gate(db, project, GateCode.G11, "EC-15.1", "Approvals: approve G11")


def ec_15_2(db: Session, project: Project) -> Condition:
    return _not_built("EC-15.2", "M6.2")


def ec_16_1(db: Session, project: Project) -> Condition:
    return _not_built("EC-16.1", "M7")


def ec_17_1(db: Session, project: Project) -> Condition:
    return Condition("EC-17.1", Level.information, True, "no condition; re-entry is the way back to redo work", None)


RULES: tuple[Rule, ...] = (
    Rule("EC-1.1", ProjectStage.idea, ProjectStage.scoped, ec_1_1),
    Rule("EC-1.2", ProjectStage.idea, ProjectStage.scoped, ec_1_2),
    Rule("EC-1.3", ProjectStage.idea, ProjectStage.scoped, ec_1_3),
    Rule("EC-2.1", ProjectStage.scoped, ProjectStage.search_planned, ec_2_1),
    Rule("EC-2.2", ProjectStage.scoped, ProjectStage.search_planned, ec_2_2),
    Rule("EC-2.3", ProjectStage.scoped, ProjectStage.search_planned, ec_2_3),
    Rule("EC-3.1", ProjectStage.search_planned, ProjectStage.retrieved, ec_3_1),
    Rule("EC-3.2", ProjectStage.search_planned, ProjectStage.retrieved, ec_3_2),
    Rule("EC-3.3", ProjectStage.search_planned, ProjectStage.retrieved, ec_3_3),
    Rule("EC-4.1", ProjectStage.retrieved, ProjectStage.screened, ec_4_1),
    Rule("EC-4.2", ProjectStage.retrieved, ProjectStage.screened, ec_4_2),
    Rule("EC-4.3", ProjectStage.retrieved, ProjectStage.screened, ec_4_3),
    Rule("EC-4.4", ProjectStage.retrieved, ProjectStage.screened, ec_4_4),
    Rule("EC-4.5", ProjectStage.retrieved, ProjectStage.screened, ec_4_5),
    Rule("EC-4.6", ProjectStage.retrieved, ProjectStage.screened, ec_4_6),
    Rule("EC-4.7", ProjectStage.retrieved, ProjectStage.screened, ec_4_7),
    Rule("EC-5.1", ProjectStage.screened, ProjectStage.extracted, ec_5_1),
    Rule("EC-5.2", ProjectStage.screened, ProjectStage.extracted, ec_5_2),
    Rule("EC-5.3", ProjectStage.screened, ProjectStage.extracted, ec_5_3),
    Rule("EC-5.4", ProjectStage.screened, ProjectStage.extracted, ec_5_4),
    Rule("EC-5.5", ProjectStage.screened, ProjectStage.extracted, ec_5_5),
    Rule("EC-6.1", ProjectStage.extracted, ProjectStage.synthesized, ec_6_1),
    Rule("EC-6.2", ProjectStage.extracted, ProjectStage.synthesized, ec_6_2),
    Rule("EC-6.3", ProjectStage.extracted, ProjectStage.synthesized, ec_6_3),
    Rule("EC-6.4", ProjectStage.extracted, ProjectStage.synthesized, ec_6_4),
    Rule("EC-7.1", ProjectStage.synthesized, ProjectStage.gaps_selected, ec_7_1),
    Rule("EC-7.2", ProjectStage.synthesized, ProjectStage.gaps_selected, ec_7_2),
    Rule("EC-8.1", ProjectStage.gaps_selected, ProjectStage.framework, ec_8_1),
    Rule("EC-8.2", ProjectStage.gaps_selected, ProjectStage.framework, ec_8_2),
    Rule("EC-9.1", ProjectStage.framework, ProjectStage.design_approved, ec_9_1),
    Rule("EC-10.1", ProjectStage.design_approved, ProjectStage.data_collected, ec_10_1),
    Rule("EC-11.1", ProjectStage.data_collected, ProjectStage.plan_locked, ec_11_1),
    Rule("EC-11.2", ProjectStage.data_collected, ProjectStage.plan_locked, ec_11_2),
    Rule("EC-12.1", ProjectStage.plan_locked, ProjectStage.analyzed, ec_12_1),
    Rule("EC-12.2", ProjectStage.plan_locked, ProjectStage.analyzed, ec_12_2),
    Rule("EC-13.1", ProjectStage.analyzed, ProjectStage.drafted, ec_13_1),
    Rule("EC-13.2", ProjectStage.analyzed, ProjectStage.drafted, ec_13_2),
    Rule("EC-13.3", ProjectStage.analyzed, ProjectStage.drafted, ec_13_3),
    Rule("EC-14.1", ProjectStage.drafted, ProjectStage.revised, ec_14_1),
    Rule("EC-15.1", ProjectStage.revised, ProjectStage.submission_ready, ec_15_1),
    Rule("EC-15.2", ProjectStage.revised, ProjectStage.submission_ready, ec_15_2),
    Rule("EC-16.1", ProjectStage.submission_ready, ProjectStage.submitted, ec_16_1),
    Rule("EC-17.1", ProjectStage.submitted, None, ec_17_1),
)


def conditions(db: Session, project: Project, target: ProjectStage) -> list[Condition]:
    """Every condition for moving `project` from its current stage to `target`, in rule order."""
    leave = project.stage
    found: list[Condition] = []
    for global_check in (_ec_0_2, _ec_0_3, _ec_0_4):
        result = global_check(db, project, target)
        if result is not None:
            found.append(result)
    for rule in RULES:
        if rule.frm != leave:
            continue
        if rule.to is not None and rule.to != target:
            continue
        found.append(rule.check(db, project))
    return found
