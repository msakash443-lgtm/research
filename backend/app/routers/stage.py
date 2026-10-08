from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import artifacts as art
from app import stage_machine
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Artifact, ArtifactStatus, Project, User
from app.schemas import (
    AdvanceRead,
    AdvanceRequest,
    ArtifactRead,
    ReentryRead,
    ReentryRequest,
    RerunStep,
    StageRead,
    StageOptionRead,
    StageStep,
)
from app import audit

router = APIRouter(prefix="/projects/{project_id}", tags=["stage"])


@router.post("/stage/reenter", response_model=ReentryRead)
def reenter_stage(
    payload: ReentryRequest,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Go back to an earlier stage (owner only: it voids approvals and marks later work stale).

    Everything from later stages, and anything built from it, becomes stale; the gates for the stages being
    redone return to pending, so a person must approve them again. Nothing is deleted.
    """
    db.refresh(project, with_for_update=True)  # one re-entry at a time per project (PostgreSQL)
    try:
        result = art.reenter_stage(db, project, payload.stage, actor=audit.user_actor(user), reason=payload.reason)
    except art.StageError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return ReentryRead(
        from_stage=result.from_stage,
        to_stage=result.to_stage,
        reason=result.reason,
        stages_to_redo=[StageStep(stage=s, gate=g) for s, g in result.stages_to_redo],
        gates_reset=result.gates_reset,
        stale_artifacts=[ArtifactRead.model_validate(a) for a in result.stale],
    )


@router.get("/artifacts", response_model=list[ArtifactRead])
def list_artifacts(
    state: ArtifactStatus | None = Query(default=None, alias="status"),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    query = select(Artifact).where(Artifact.project_id == project.id)
    if state is not None:
        query = query.where(Artifact.status == state)
    return db.scalars(query.order_by(Artifact.created_at.asc(), Artifact.id.asc())).all()


@router.get("/rerun-path", response_model=list[RerunStep])
def get_rerun_path(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """What has to be redone, in workflow order, with each stage's gate and its status."""
    steps = art.rerun_path(db, project)
    db.commit()  # rerun_path may create missing gate rows
    return [
        RerunStep(
            stage=step["stage"], gate=step["gate"], gate_status=step["gate_status"],
            artifacts=[ArtifactRead.model_validate(a) for a in step["artifacts"]],
        )
        for step in steps
    ]


@router.get("/stage", response_model=StageRead)
def get_stage(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """The current stage and where it can go next, with each next stage's gate and whether it is ready."""
    options = stage_machine.next_options(db, project)
    db.commit()  # next_options may create missing gate rows
    return StageRead(
        stage=project.stage,
        next=[StageOptionRead(stage=o.stage, gate=o.gate, gate_status=o.gate_status, ready=o.ready) for o in options],
    )


@router.post("/stage/advance", response_model=AdvanceRead)
def advance_stage(
    payload: AdvanceRequest | None = None,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Move one stage forward. A stage with a gate can be reached only after a person has approved that gate."""
    db.refresh(project, with_for_update=True)  # serialise with other stage changes on PostgreSQL
    try:
        result = stage_machine.advance_stage(
            db, project, payload.stage if payload else None, actor=audit.user_actor(user)
        )
    except stage_machine.GateNotApproved as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "gate": exc.gate.value,
                "gate_status": exc.gate_status.value,
                "required_roles": sorted(r.value for r in exc.roles),
            },
        ) from exc
    except stage_machine.StaleResults as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "stale_artifacts": [ArtifactRead.model_validate(a).model_dump(mode="json") for a in exc.artifacts],
            },
        ) from exc
    except art.StageError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return AdvanceRead(from_stage=result.from_stage, to_stage=result.to_stage, gate=result.gate)
