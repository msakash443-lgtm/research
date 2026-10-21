from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit, exit_conditions, prisma, section_approval
from app.database import get_db
from app.extraction_verification import g4_problem
from app.dependencies import READ_ROLES, current_user, project_access, project_role, refuse_if_archived
from app.artifacts import stage_index
from app.gates import (
    GATE_STAGES, approved_later_gates, ensure_gates, pending_earlier_gates, reblock_tasks_for_gate, release_tasks_for_gate,
    required_roles,
)
from app.models import AnalysisPlan, Document, Gate, GateCode, GateStatus, MetaAnalysisPlan, Project, ProjectRole, User, utcnow
from app.stage_order import StageNotInChain, require_stage
from app.schemas import GateDecision, GateRead, GateReopen

router = APIRouter(prefix="/projects/{project_id}/gates", tags=["gates"])

# The gate row for G3/G4/G6/G9 approval itself is excluded from the Required check below: it is unmet
# until this approval. For G6 this makes EC-8.2 (research question linked to a selected gap, M4.8) and
# EC-8.3 (conceptual-model path with construct-measure alignment, M4.8) real blockers on approving G6, not
# just on advancing past it. For G9 this makes EC-12.2 (analysis runs post-date G8) and EC-12.3
# (findings-to-literature map confirmed by a person, M6.9) real blockers on approving G9, likewise.
_OWN_GATE_ROW = {GateCode.G3: "EC-4.1", GateCode.G4: "EC-5.1", GateCode.G6: "EC-8.1", GateCode.G9: "EC-12.1"}


def has_locked_analysis_plan(db: Session, project_id) -> bool:
    """True when this project has a locked analysis plan (M5.3.1 lock) or a meta-analysis plan.

    A meta-analysis plan has no lock step: its router freezes the plan by G8 itself, and it refuses
    new plans once G8 is approved, so a plan's existence is what G8 approval needs here.
    """
    locked = select(AnalysisPlan.id).where(AnalysisPlan.project_id == project_id, AnalysisPlan.locked_at.is_not(None))
    meta = select(MetaAnalysisPlan.id).where(MetaAnalysisPlan.project_id == project_id)
    return db.scalar(locked.limit(1)) is not None or db.scalar(meta.limit(1)) is not None


def _reopen_problem(project: Project, gates: list[Gate], gate: Gate) -> str | None:
    """Why an approved gate can't be reopened right now, or None if it can (role aside)."""
    if gate.status != GateStatus.approved:
        return f"Gate {gate.code.value} is not approved; there is nothing to reopen"
    stage = GATE_STAGES[gate.code]
    if stage_index(project.stage, project) >= stage_index(stage, project):
        return (
            f"The project has already reached '{stage.value}', which gate {gate.code.value} completes; "
            "go back with a re-entry instead (owner)"
        )
    later = approved_later_gates(gates, gate.code, project)
    if later:
        return f"Reopen the later approved gate(s) first: {', '.join(c.value for c in later)}"
    return None


def _gate_read(gate: Gate, role: ProjectRole | None, project: Project, gates: list[Gate]) -> GateRead:
    roles = required_roles(gate.code)
    waiting_for = pending_earlier_gates(gates, gate.code, project)
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
    db: Session, project: Project, user: User, code: GateCode, outcome: GateStatus, note: str | None,
    confirm_no_human_participants: bool = False,
) -> GateRead:
    """Record a person's decision on a gate.

    Gates are decided only here, only for a signed-in user whose role the gate requires,
    and never by the worker, executor or any agent code (a test enforces that no agent
    module imports this). Nothing approves a gate on timeout or on a human's behalf.
    """
    try:
        require_stage(project, GATE_STAGES[code])
    except StageNotInChain:
        audit.record(
            db, actor=audit.user_actor(user), action="stage.not_in_chain", project_id=project.id,
            payload={"stage": GATE_STAGES[code].value, "gate": code.value},
        )
        raise
    role = project_role(db, project, user)
    if role not in required_roles(code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Gate {code.value} cannot be decided by your role")
    # Serialise with reopen and stage changes on PostgreSQL: the ordering check below reads the other gates,
    # so a concurrent reopen of an earlier gate must not commit between that read and our write (M0.5.13).
    db.refresh(project, with_for_update=True)
    gates = ensure_gates(db, project)
    gate = next(g for g in gates if g.code == code)
    if code == GateCode.G4 and outcome == GateStatus.approved:
        db.refresh(gate, with_for_update=True)
    if gate.status == GateStatus.approved:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Gate {code.value} is already approved")
    waiting_for = pending_earlier_gates(gates, code, project)
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
    if code == GateCode.G7 and outcome == GateStatus.approved:
        from app.research_types import human_participants  # local: research_types imports GATE_STAGES from this module's sibling

        if not human_participants(project):
            if not confirm_no_human_participants:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="This research type has no human participants. Approving G7 needs an explicit "
                    "'no human participants' confirmation instead of a full ethics pack.",
                )
            note = note or "No human participants in this study (confirmed at G7 approval)."
    if outcome == GateStatus.approved and code in _OWN_GATE_ROW:
        unmet = [
            c
            for c in exit_conditions.conditions(db, project, GATE_STAGES[code])
            if (
                (c.level is exit_conditions.Level.required or (code == GateCode.G6 and c.id == "EC-8.2"))
                and not c.met
                and c.id != _OWN_GATE_ROW[code]
            )
        ]
        if unmet:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"Gate {code.value} cannot be approved while its stage's required conditions are unmet: {', '.join(c.id for c in unmet)}",
                    "required": [{"id": c.id, "message": c.message, "fix": c.fix} for c in unmet],
                },
            )
    if outcome == GateStatus.approved and code is GateCode.G3:
        missing = prisma.flow_counts(db, project)["screening"]["exclusion_reasons_missing"]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"Gate G3 cannot be approved while {missing} excluded record(s) have no valid reason code",
                    "exclusion_reasons_missing": missing,
                },
            )
    if code == GateCode.G4 and outcome == GateStatus.approved:
        problem = g4_problem(db, project)
        if problem:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
    if code == GateCode.G8 and outcome == GateStatus.approved and not has_locked_analysis_plan(db, project.id):
        # M5.3.4: the plan must be locked before the gate that freezes it can pass; lock-first is no longer only convention.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gate G8 cannot be approved until an analysis plan is locked. Create the plan and lock it first.",
        )
    if code == GateCode.G10 and outcome == GateStatus.approved:
        # M6.11, spec 5.11: a human gate per section; the project-wide gate needs every section approved.
        sections = db.scalars(select(Document.id).where(Document.project_id == project.id)).all()
        if not sections:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Gate G10 cannot be approved until the manuscript has at least one section",
            )
        unapproved = section_approval.unapproved_documents(db, project)
        if unapproved:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"Gate G10 cannot be approved while {len(unapproved)} section(s) are unapproved",
                    "documents": [{"document_id": str(u.document_id), "title": u.title, "reason": u.reason} for u in unapproved],
                },
            )

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
        payload={
            "gate": code.value, "role": role.value, "note": note,
            **({"no_human_participants_confirmed": True} if code == GateCode.G7 and confirm_no_human_participants else {}),
        },
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
    refuse_if_archived(project)
    return decide_gate(
        db, project, user, code, GateStatus.approved,
        payload.note if payload else None,
        payload.confirm_no_human_participants if payload else False,
    )


@router.post("/{code}/reject", response_model=GateRead)
def reject_gate(
    code: GateCode,
    payload: GateDecision | None = None,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    refuse_if_archived(project)
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
    refuse_if_archived(project)
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
