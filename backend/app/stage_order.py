"""The single source of stage and gate order (X.49, decision 4a of the X.40 amendment).

Every module that needs "what comes before/after what" reads it from here. Only this module may
iterate `ProjectStage` for ordering; `tests/test_stage_order.py` enforces that. Branching stages
(submitted, revision_loop) are expressed in `next_stages`, not in ad hoc callers.
"""

from __future__ import annotations

from app.models import GateCode, Project, ProjectStage

# The full journey, in Appendix C order. Every project follows it today; `chain(project)` is the
# hook for per-project variants (X.43) without touching callers.
_JOURNEY: tuple[ProjectStage, ...] = tuple(ProjectStage)

# Stages that branch rather than advance one step. Keep in sync with the workflow in
# docs/research-automation-spec.md Appendix C.
_BRANCHES: dict[ProjectStage, tuple[ProjectStage, ...]] = {
    ProjectStage.submitted: (ProjectStage.revision_loop, ProjectStage.accepted),
    ProjectStage.revision_loop: (ProjectStage.accepted,),  # to resubmit, re-enter and move forward again
    ProjectStage.accepted: (),
}


def chain(project: Project | None = None) -> list[ProjectStage]:
    """The stages a project passes through, in order. Same for every project until X.43."""
    return list(_JOURNEY)


def index(project: Project | None, stage: ProjectStage) -> int:
    return chain(project).index(stage)


def next_stages(project: Project | None, stage: ProjectStage) -> list[ProjectStage]:
    """The stages a project may move to from `stage`, in the order they are offered."""
    if stage in _BRANCHES:
        return list(_BRANCHES[stage])
    order = chain(project)
    return [order[order.index(stage) + 1]]


def gate_order(gate_stages: dict[GateCode, ProjectStage], project: Project | None = None) -> list[GateCode]:
    """Gates in the order their stages are reached. Derived, so reordering stages reorders gates."""
    position = {stage: i for i, stage in enumerate(chain(project))}
    return sorted(gate_stages, key=lambda code: position[gate_stages[code]])
