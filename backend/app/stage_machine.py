"""Moving a project forward through the workflow (spec Appendix C).

    IDEA -> SCOPED(G1) -> SEARCH_PLANNED(G2) -> RETRIEVED -> SCREENED(G3) -> EXTRACTED(G4) -> SYNTHESIZED
    -> GAPS_SELECTED(G5) -> FRAMEWORK(G6) -> DESIGN_APPROVED(G7) -> DATA_COLLECTED -> PLAN_LOCKED(G8)
    -> ANALYZED(G9) -> DRAFTED(G10) -> REVISED -> SUBMISSION_READY(G11) -> SUBMITTED -> [REVISION_LOOP | ACCEPTED]

One step at a time. Reaching a stage that has a gate needs that gate approved by a person; the same
function serves the API and system producers, and a producer can advance an ungated stage but can never
satisfy a gate (only `decide_gate` approves). Going back is `artifacts.reenter_stage`; after a re-entry the
project can't move into or past a stage whose results are still stale until each is replaced (plan M0.5.9).

`advance_stage` is not idempotent on its own (each call is one more step); callers that may retry pass the
target stage, so a repeated request is refused instead of moving twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import audit
from app.artifacts import StageError, gate_for_stage, unreplaced_stale
from app import stage_order
from app import exit_conditions as ec
from app.gates import ensure_gates, required_roles
from app.models import Artifact, GateCode, GateStatus, Project, ProjectRole, ProjectStage


class GateNotApproved(StageError):
    """The stage has a gate that no person has approved yet."""

    def __init__(self, target: ProjectStage, gate: GateCode, gate_status: GateStatus, roles: frozenset[ProjectRole]):
        self.target, self.gate, self.gate_status, self.roles = target, gate, gate_status, roles
        names = " or ".join(sorted(r.value for r in roles))
        super().__init__(f"Gate {gate.value} must be approved by an {names} before the project can reach '{target.value}'")


class StaleResults(StageError):
    """Results of the target stage, or of one before it, went stale on a re-entry and haven't been replaced."""

    def __init__(self, target: ProjectStage, artifacts: list[Artifact]):
        self.target, self.artifacts = target, artifacts
        stages = ", ".join(dict.fromkeys(f"'{a.stage.value}'" for a in artifacts))
        super().__init__(
            f"{len(artifacts)} result(s) from {stages} are stale since a re-entry and haven't been redone; "
            f"redo them before the project can reach '{target.value}'"
        )


@dataclass
class Advance:
    from_stage: ProjectStage
    to_stage: ProjectStage
    gate: GateCode | None


@dataclass
class StageOption:
    stage: ProjectStage
    gate: GateCode | None
    gate_status: GateStatus | None
    ready: bool  # True when the project could move there right now


def allowed_next(project: Project) -> list[ProjectStage]:
    """The stages a project can move forward to from its current stage."""
    stage = project.stage
    if stage == ProjectStage.accepted:
        return []
    if stage == ProjectStage.submitted:
        return [candidate for candidate in (ProjectStage.revision_loop, ProjectStage.accepted) if candidate in stage_order.chain(project)]
    if stage == ProjectStage.revision_loop:
        return [ProjectStage.accepted] if ProjectStage.accepted in stage_order.chain(project) else []
    return stage_order.next_stages(project, stage)


def next_options(db: Session, project: Project) -> list[StageOption]:
    gates = {g.code: g for g in ensure_gates(db, project)}
    options = []
    for target in allowed_next(project):
        code = gate_for_stage(target)
        gate_status = gates[code].status if code else None
        gate_ok = code is None or gate_status == GateStatus.approved
        options.append(StageOption(target, code, gate_status, ready=gate_ok and not unreplaced_stale(db, project, target)))
    return options


class ConditionsUnmet(StageError):
    """The target stage's exit conditions are unmet: Required ones always, Recommended ones until overridden."""

    def __init__(self, target: ProjectStage, required: list[ec.Condition], recommended: list[ec.Condition]):
        self.target = target
        self.required = required
        self.recommended = recommended
        super().__init__(
            f"'{target.value}' is locked by exit conditions: " + ", ".join(c.id for c in required + recommended)
        )


class ConditionOverrideInvalid(ValueError):
    """override_reasons name a condition that is not an unmet Recommended condition of this move."""


def advance_stage(
    db: Session, project: Project, target: ProjectStage | None = None, *, actor: str, override_reasons: dict[str, str] | None = None
) -> Advance:
    """Move the project one stage forward. The caller commits."""
    current = project.stage
    choices = allowed_next(project)
    if not choices:
        raise StageError(f"'{current.value}' is the final stage")
    if target is None:
        if len(choices) != 1:
            raise StageError("Choose where to go next: " + ", ".join(f"'{c.value}'" for c in choices))
        target = choices[0]
    if target not in choices:
        raise StageError(
            f"'{target.value}' does not follow '{current.value}'; next is " + " or ".join(f"'{c.value}'" for c in choices)
        )

    stale = unreplaced_stale(db, project, target)
    if stale:
        raise StaleResults(target, stale)

    code = gate_for_stage(target)
    decided_by = None
    if code is not None:
        gate = next(g for g in ensure_gates(db, project) if g.code == code)
        if gate.status != GateStatus.approved:
            raise GateNotApproved(target, code, gate.status, required_roles(code))
        decided_by = gate.decided_by
    conditions = ec.conditions(db, project, target)
    required_unmet = [c for c in conditions if c.level is ec.Level.required and not c.met]
    if required_unmet:
        raise ConditionsUnmet(target, required_unmet, [])
    recommended_unmet = [c for c in conditions if c.level is ec.Level.recommended and not c.met]
    reasons = override_reasons or {}
    unknown = sorted(set(reasons) - {c.id for c in recommended_unmet})
    if unknown:
        raise ConditionOverrideInvalid("override_reasons name no unmet Recommended condition: " + ", ".join(unknown))
    unanswered = [c for c in recommended_unmet if c.id not in reasons]
    if unanswered:
        raise ConditionsUnmet(target, [], unanswered)
    for c in recommended_unmet:
        audit.record(
            db, actor=actor, action="stage.condition_overridden", project_id=project.id,
            payload={"condition": c.id, "from": current.value, "to": target.value, "reason": reasons[c.id]},
        )

    project.stage = target
    project.touch()
    audit.record(
        db, actor=actor, action="project.advanced", project_id=project.id,
        payload={"from": current.value, "to": target.value, "gate": code.value if code else None, "gate_decided_by": decided_by},
    )
    db.flush()
    return Advance(current, target, code)
