from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, current_user, project_access, project_role
from app.gates import ensure_gates, release_tasks_for_gate, required_roles
from app.models import Gate, GateCode, GateStatus, Project, ProjectRole, User, utcnow
from app.schemas import GateDecision, GateRead

router = APIRouter(prefix="/projects/{project_id}/gates", tags=["gates"])


def _gate_read(gate: Gate, role: ProjectRole | None) -> GateRead:
    roles = required_roles(gate.code)
    return GateRead(
        code=gate.code,
        status=gate.status,
        decided_by=gate.decided_by,
        decided_at=gate.decided_at,
        note=gate.note,
        required_roles=sorted(roles, key=lambda r: r.value),
        can_decide=role in roles and gate.status != GateStatus.approved,
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
    return [_gate_read(gate, role) for gate in gates]


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
    gate = next(g for g in ensure_gates(db, project) if g.code == code)
    if gate.status == GateStatus.approved:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Gate {code.value} is already approved")
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
    return _gate_read(gate, role)


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
