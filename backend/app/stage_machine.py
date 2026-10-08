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
from app.artifacts import STAGE_ORDER, StageError, gate_for_stage, unreplaced_stale
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


def allowed_next(stage: ProjectStage) -> list[ProjectStage]:
    """The stages a project can move forward to from `stage`."""
    if stage == ProjectStage.accepted:
        return []
    if stage == ProjectStage.submitted:
        return [ProjectStage.revision_loop, ProjectStage.accepted]
    if stage == ProjectStage.revision_loop:
        return [ProjectStage.accepted]  # to resubmit, go back with a re-entry and move forward again
    return [STAGE_ORDER[STAGE_ORDER.index(stage) + 1]]


def next_options(db: Session, project: Project) -> list[StageOption]:
    gates = {g.code: g for g in ensure_gates(db, project)}
    options = []
    for target in allowed_next(project.stage):
        code = gate_for_stage(target)
        gate_status = gates[code].status if code else None
        gate_ok = code is None or gate_status == GateStatus.approved
        options.append(StageOption(target, code, gate_status, ready=gate_ok and not unreplaced_stale(db, project, target)))
    return options


def advance_stage(db: Session, project: Project, target: ProjectStage | None = None, *, actor: str) -> Advance:
    """Move the project one stage forward. The caller commits."""
    current = project.stage
    choices = allowed_next(current)
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

    project.stage = target
    project.touch()
    audit.record(
        db, actor=actor, action="project.advanced", project_id=project.id,
        payload={"from": current.value, "to": target.value, "gate": code.value if code else None, "gate_decided_by": decided_by},
    )
    db.flush()
    return Advance(current, target, code)
