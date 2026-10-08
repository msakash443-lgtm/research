"""Who may decide each human gate, and gate bootstrap for new projects.

Policy (agreed 2026-10-03): scientific-direction and content gates can be decided by an
owner or a supervisor; the commitments that lock the study or go out under the authors'
names (ethics/design G7, analysis plan G8, final sign-off G11) need an owner.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.models import Gate, GateCode, GateStatus, Project, ProjectRole, ProjectStage, Task, TaskStatus
from app.task_registry import REQUIRED_GATES

OWNER_OR_SUPERVISOR = frozenset({ProjectRole.owner, ProjectRole.supervisor})
OWNER_ONLY = frozenset({ProjectRole.owner})

GATE_APPROVER_ROLES: dict[GateCode, frozenset[ProjectRole]] = {
    GateCode.G1: OWNER_OR_SUPERVISOR,
    GateCode.G2: OWNER_OR_SUPERVISOR,
    GateCode.G3: OWNER_OR_SUPERVISOR,
    GateCode.G4: OWNER_OR_SUPERVISOR,
    GateCode.G5: OWNER_OR_SUPERVISOR,
    GateCode.G6: OWNER_OR_SUPERVISOR,
    GateCode.G7: OWNER_ONLY,
    GateCode.G8: OWNER_ONLY,
    GateCode.G9: OWNER_OR_SUPERVISOR,
    GateCode.G10: OWNER_OR_SUPERVISOR,
    GateCode.G11: OWNER_ONLY,
}


def required_roles(code: GateCode) -> frozenset[ProjectRole]:
    return GATE_APPROVER_ROLES[code]


def new_gates() -> list[Gate]:
    """All eleven gates, pending, for a project that is being created."""
    return [Gate(code=code) for code in GateCode]


def ensure_gates(db: Session, project: Project) -> list[Gate]:
    """The project's gates in workflow order, creating any that are missing.

    Projects made outside `create_project` (databases built by create_all, direct inserts)
    have no gate rows yet. The caller commits.
    """
    existing = {g.code: g for g in db.scalars(select(Gate).where(Gate.project_id == project.id))}
    missing = [code for code in GateCode if code not in existing]
    if missing:
        try:
            with db.begin_nested():  # savepoint: a concurrent request may insert the same gates
                db.add_all(Gate(project_id=project.id, code=code) for code in missing)
        except IntegrityError:
            pass  # the other request won; read its rows below
        existing = {g.code: g for g in db.scalars(select(Gate).where(Gate.project_id == project.id))}
    return [existing[code] for code in GateCode]


def gate_is_approved(db: Session, project_id, code: GateCode) -> bool:
    """True only if a person has approved this gate. A missing gate row counts as not approved."""
    return db.scalar(select(Gate.status).where(Gate.project_id == project_id, Gate.code == code)) == GateStatus.approved


# ---- ordering and reopening (M0.5.10, Decisions log 2026-10-07) ------------------------------------

GATE_ORDER: list[GateCode] = list(GateCode)


def pending_earlier_gates(gates: list[Gate], code: GateCode) -> list[GateCode]:
    """Earlier gates not yet approved. A gate can be decided only when this is empty (gates go in order)."""
    status = {g.code: g.status for g in gates}
    return [c for c in GATE_ORDER[: GATE_ORDER.index(code)] if status.get(c) != GateStatus.approved]


def approved_later_gates(gates: list[Gate], code: GateCode) -> list[GateCode]:
    """Later gates already approved. Reopening `code` waits until these are reopened (no cascade)."""
    status = {g.code: g.status for g in gates}
    return [c for c in GATE_ORDER[GATE_ORDER.index(code) + 1 :] if status.get(c) == GateStatus.approved]


def reblock_tasks_for_gate(db: Session, project_id, code: GateCode, actor: str) -> int:
    """A reopened gate holds its tasks again: queued tasks of a type that needs `code` go back to `blocked`.

    A task already running finishes its attempt (it was released by a then-valid approval); if it is
    retried, the worker's claim-time gate check blocks it. The caller commits.
    """
    types = [task_type for task_type, gate in REQUIRED_GATES.items() if gate == code]
    if not types:
        return 0
    queued = db.scalars(
        select(Task).where(Task.project_id == project_id, Task.status == TaskStatus.queued, Task.type.in_(types))
    ).all()
    for task in queued:
        task.status = TaskStatus.blocked
        task.blocked_by_gate = code
    if queued:
        audit.record(
            db, actor=actor, action="task.blocked", project_id=project_id,
            payload={"gate": code.value, "blocked": len(queued), "task_ids": [str(t.id) for t in queued], "via": "gate.reopened"},
        )
    return len(queued)


def release_tasks_for_gate(db: Session, project_id, code: GateCode, actor: str) -> int:
    """Queue the tasks that were waiting on `code`. Called by `decide_gate` right after a person approves it.

    Nothing else may call this: a blocked task is released by a human approval and nothing
    else (plan rule 21). The caller commits.
    """
    blocked = db.scalars(
        select(Task).where(Task.project_id == project_id, Task.status == TaskStatus.blocked, Task.blocked_by_gate == code)
    ).all()
    for task in blocked:
        task.status = TaskStatus.queued
        task.blocked_by_gate = None
        task.run_after = None
    if blocked:
        audit.record(
            db, actor=actor, action="task.released", project_id=project_id,
            payload={"gate": code.value, "released": len(blocked), "task_ids": [str(t.id) for t in blocked]},
        )
    return len(blocked)


def release_tasks_for_approved_gates(db: Session) -> int:
    """Worker sweep: queue blocked tasks whose gate a person has *already* approved.

    This approves nothing. It only heals the race where a task was blocked just as its gate
    was being approved (so `decide_gate` found nothing to release). The caller commits.
    """
    stuck = db.scalars(
        select(Task)
        .join(Gate, (Gate.project_id == Task.project_id) & (Gate.code == Task.blocked_by_gate))
        .where(Task.status == TaskStatus.blocked, Gate.status == GateStatus.approved)
    ).all()
    for task in stuck:
        code = task.blocked_by_gate
        task.status = TaskStatus.queued
        task.blocked_by_gate = None
        task.run_after = None
        audit.record(
            db, actor=audit.SYSTEM_WORKER, action="task.released", project_id=task.project_id,
            payload={"gate": code.value, "released": 1, "task_ids": [str(task.id)], "via": "sweep"},
        )
    return len(stuck)


# The stage each gate completes (spec Appendix C: SCOPED(G1) -> SEARCH_PLANNED(G2) -> ... -> SUBMISSION_READY(G11)).
# A project that is *at* a stage has passed that stage's gate; going back before the stage voids the approval.
GATE_STAGES: dict[GateCode, ProjectStage] = {
    GateCode.G1: ProjectStage.scoped,
    GateCode.G2: ProjectStage.search_planned,
    GateCode.G3: ProjectStage.screened,
    GateCode.G4: ProjectStage.extracted,
    GateCode.G5: ProjectStage.gaps_selected,
    GateCode.G6: ProjectStage.framework,
    GateCode.G7: ProjectStage.design_approved,
    GateCode.G8: ProjectStage.plan_locked,
    GateCode.G9: ProjectStage.analyzed,
    GateCode.G10: ProjectStage.drafted,
    GateCode.G11: ProjectStage.submission_ready,
}
