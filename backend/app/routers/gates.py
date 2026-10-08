from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, current_user, project_access, project_role
from app.artifacts import stage_index
from app.gates import (
    GATE_STAGES, approved_later_gates, ensure_gates, pending_earlier_gates, reblock_tasks_for_gate, release_tasks_for_gate,
    required_roles,
)
from app.models import Gate, GateCode, GateStatus, Project, ProjectRole, User, utcnow
from app.schemas import GateDecision, GateRead, GateReopen

router = APIRouter(prefix="/projects/{project_id}/gates", tags=["gates"])


def _reopen_problem(project: Project, gates: list[Gate], gate: Gate) -> str | None:
    """Why an approved gate can't be reopened right now, or None if it can (role aside)."""
    if gate.status != GateStatus.approved:
        return f"Gate {gate.code.value} is not approved; there is nothing to reopen"
    stage = GATE_STAGES[gate.code]
    if stage_index(project.stage) >= stage_index(stage):
        return (
            f"The project has already reached '{stage.value}', which gate {gate.code.value} completes; "
            "go back with a re-entry instead (owner)"
        )
    later = approved_later_gates(gates, gate.code)
    if later:
        return f"Reopen the later approved gate(s) first: {', '.join(c.value for c in later)}"
    return None


def _gate_read(gate: Gate, role: ProjectRole | None, project: Project, gates: list[Gate]) -> GateRead:
    roles = required_roles(gate.code)
    waiting_for = pending_earlier_gates(gates, gate.code)
    return GateRead(
        code=gate.code,
        status=gate.status,
        decided_by=gate.decided_by,
        decided_at=gate.decided_at,
        note=gate.note,
        required_roles=sorted(roles, key=lambda r: r.value),
        can_decide=role in roles and gate.status != GateStatus.approved and not waiting_for,
        waiting_for=waiting_for,
        can_reopen=role in roles and _reopen_problem(project, gates, gate) is None,
    )


@router.get("", response_model=list[GateRead])
def list_gates(
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    gates = ensure_gates(db, project)
    db.commit()
    role = project_role(db, project, user)
    return [_gate_read(gate, role, project, gates) for gate in gates]


def decide_gate(
    db: Session, project: Project, user: User, code: GateCode, outcome: GateStatus, note: str | None
) -> GateRead:
    """Record a person's decision on a gate.

    Gates are decided only here, only for a signed-in user whose role the gate requires,
    and never by the worker, executor or any agent code (a test enforces that no agent
    module imports this). Nothing approves a gate on timeout or on a human's behalf.
    """
    role = project_role(db, project, user)
    if role not in required_roles(code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Gate {code.value} cannot be decided by your role")
    # Serialise with reopen and stage changes on PostgreSQL: the ordering check below reads the other gates,
    # so a concurrent reopen of an earlier gate must not commit between that read and our write (M0.5.13).
    db.refresh(project, with_for_update=True)
    gates = ensure_gates(db, project)
    gate = next(g for g in gates if g.code == code)
    if gate.status == GateStatus.approved:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Gate {code.value} is already approved")
    waiting_for = pending_earlier_gates(gates, code)
    if waiting_for:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Gates are decided in order: approve {', '.join(c.value for c in waiting_for)} before {code.value}",
                "waiting_for": [c.value for c in waiting_for],
            },
        )
    if outcome == GateStatus.rejected and not note:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rejecting a gate needs a note explaining why")

    # Conditional write: another request may have approved this gate since we read it.
    # The WHERE clause is re-checked by the database at write time, so an approval can't be
    # overwritten by a concurrent decision; the loser gets the same 409 as a late request.
    written = db.execute(
        update(Gate)
        .where(Gate.id == gate.id, Gate.status != GateStatus.approved)
        .values(status=outcome, decided_by=audit.user_actor(user), decided_at=utcnow(), note=note)
        .execution_options(synchronize_session=False)
    ).rowcount
    if written != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Gate {code.value} is already approved")
    audit.record(
        db,
        actor=audit.user_actor(user),
        action=f"gate.{outcome.value}",
        project_id=project.id,
        payload={"gate": code.value, "role": role.value, "note": note},
    )
    if outcome == GateStatus.approved:
        release_tasks_for_gate(db, project.id, code, audit.user_actor(user))
    project.touch()
    db.commit()
    db.refresh(gate)
    return _gate_read(gate, role, project, ensure_gates(db, project))


@router.post("/{code}/approve", response_model=GateRead)
def approve_gate(
    code: GateCode,
    payload: GateDecision | None = None,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return decide_gate(db, project, user, code, GateStatus.approved, payload.note if payload else None)


@router.post("/{code}/reject", response_model=GateRead)
def reject_gate(
    code: GateCode,
    payload: GateDecision | None = None,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return decide_gate(db, project, user, code, GateStatus.rejected, payload.note if payload else None)


@router.post("/{code}/reopen", response_model=GateRead)
def reopen_gate(
    code: GateCode,
    payload: GateReopen,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Take back an approval before the project has acted on it (M0.5.10).

    Only a person with the gate's required role, with a reason, while the project hasn't reached the
    stage the gate completes and no later gate is approved. The gate goes back to pending, queued tasks
    waiting on it are held again, and the audit log keeps the previous decision.
    """
    role = project_role(db, project, user)
    if role not in required_roles(code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Gate {code.value} cannot be reopened by your role")
    db.refresh(project, with_for_update=True)  # serialise with stage changes on PostgreSQL
    gates = ensure_gates(db, project)
    gate = next(g for g in gates if g.code == code)
    problem = _reopen_problem(project, gates, gate)
    if problem:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
    previous = {"decided_by": gate.decided_by, "decided_at": gate.decided_at.isoformat() if gate.decided_at else None, "note": gate.note}

    # Conditional write, as in decide_gate: only an approval that is still in place is reopened.
    written = db.execute(
        update(Gate)
        .where(Gate.id == gate.id, Gate.status == GateStatus.approved)
        .values(status=GateStatus.pending, decided_by=None, decided_at=None, note=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    if written != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Gate {code.value} is no longer approved")
    actor = audit.user_actor(user)
    audit.record(
        db, actor=actor, action="gate.reopened", project_id=project.id,
        payload={"gate": code.value, "role": role.value, "reason": payload.reason, "previous": previous},
    )
    reblock_tasks_for_gate(db, project.id, code, actor)
    project.touch()
    db.commit()
    db.refresh(gate)
    return _gate_read(gate, role, project, ensure_gates(db, project))
