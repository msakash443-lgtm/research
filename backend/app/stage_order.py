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
class StageNotInChain(ValueError):
    """The requested work targets a stage omitted from this project's frozen chain."""

    def __init__(self, project: Project, stage: ProjectStage):
        self.project_id = project.id
        self.requested_stage = stage
        super().__init__(f"Stage '{stage.value}' is not part of research type '{project.research_type}'")


def require_stage(project: Project, stage: ProjectStage) -> None:
    if stage not in chain(project):
        raise StageNotInChain(project, stage)


def chain(project: Project | None = None) -> list[ProjectStage]:
    """The stages a project passes through, using its frozen research-type snapshot when present."""
    if project is None or not project.stage_plan_json:
        return list(_JOURNEY)
    from app.research_types import ResearchType, _check

    snapshot = ResearchType(**project.stage_plan_json)
    if snapshot.name != project.research_type:
        raise ValueError("project research type and stage snapshot do not match")
    _check(snapshot, "project stage snapshot")
    return snapshot.chain


def index(project: Project | None, stage: ProjectStage) -> int:
    return chain(project).index(stage)


def next_stages(project: Project | None, stage: ProjectStage) -> list[ProjectStage]:
    """The stages a project may move to from `stage`, in the order they are offered."""
    if stage in _BRANCHES:
        return [candidate for candidate in _BRANCHES[stage] if candidate in chain(project)]
    order = chain(project)
    position = order.index(stage)
    return order[position + 1:position + 2]


def gate_order(gate_stages: dict[GateCode, ProjectStage], project: Project | None = None) -> list[GateCode]:
    """Gates in the order their stages are reached, restricted to the project's chain."""
    position = {stage: i for i, stage in enumerate(chain(project))}
    return sorted((code for code, stage in gate_stages.items() if stage in position), key=lambda code: position[gate_stages[code]])


