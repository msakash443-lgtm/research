from __future__ import annotations

import dataclasses

from sqlalchemy import or_, select
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import artifacts as art
from app import stage_machine
from app.database import get_db
from app.config import get_settings
from app.dependencies import MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Artifact, ArtifactStatus, AuditEvent, Project, ProjectStage, User
from app.gates import GATE_STAGES
from app import guidance, research_types, stage_order
from app import exit_conditions as ec
from app.schemas import (
    AdvanceRead,
    AuditEventRead,
    AdvanceRequest,
    ConditionRead,
    StageChainItem,
    ArtifactRead,
    ReentryRead,
    ReentryRequest,
    RerunStep,
    StageRead,
    StageOptionRead,
    StageStep,
    SuggestionRead,
)
from app import audit
from app.ai_controls import AIOffError, llm_for_project, require_ai_on
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.stage_coach import StageCoachError, coach

router = APIRouter(prefix="/projects/{project_id}", tags=["stage"])


def _condition_read(c: ec.Condition) -> ConditionRead:
    return ConditionRead(id=c.id, level=c.level, met=c.met, message=c.message, fix=c.fix)


def _condition_rows(db: Session, project: Project, target: ProjectStage) -> list[ConditionRead]:
    """Exit conditions for moving `project` to `target`, as API rows (X.48.2)."""
    return [_condition_read(c) for c in ec.conditions(db, project, target)]


def _condition_payload(c: ec.Condition) -> dict:
    return _condition_read(c).model_dump(mode="json")


@router.post("/stage/reenter", response_model=ReentryRead)
def reenter_stage(
    payload: ReentryRequest,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Reopen the current stage or go back to an earlier one (owner only: it voids approvals and marks work stale).

    Everything from the target stage onward, and anything built from it, becomes stale; the gates of those
    stages (the target's own included) return to pending, so a person must approve them again.
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


STAGE_HISTORY_ACTIONS = ("project.advanced", "project.reentered", "stage.condition_overridden")


@router.get("/stage/history", response_model=list[AuditEventRead])
def get_stage_history(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Every move of the project through its stages, oldest first: advances, re-entries and reopens (with
    reasons), condition overrides, and every gate decision, reset and reopen. Read from the audit log."""
    return db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.project_id == project.id,
            or_(AuditEvent.action.in_(STAGE_HISTORY_ACTIONS), AuditEvent.action.like("gate.%")),
        )
        .order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc())
    ).all()


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
    gate_by_stage = {stage: code for code, stage in GATE_STAGES.items()}
    evaluated = [(o, ec.conditions(db, project, o.stage)) for o in options]
    suggestions = guidance.next_steps([(o.stage, conds) for o, conds in evaluated], guidance.load_guide())
    return StageRead(
        stage=project.stage,
        next=[
            StageOptionRead(
                stage=o.stage,
                gate=o.gate,
                gate_status=o.gate_status,
                ready=o.ready,
                conditions=[_condition_read(c) for c in conds],
            )
            for o, conds in evaluated
        ],
        chain=[
            StageChainItem(stage=step.stage, gate=step.gate, label=step.label)
            for step in research_types.ResearchType(**project.stage_plan_json).stages
        ],

        suggestions=[SuggestionRead(**dataclasses.asdict(s)) for s in suggestions],
        human_participants=research_types.human_participants(project),
        research_type=project.research_type,
    )

class StageCoachActionRead(BaseModel):
    text: str
    condition_id: str

class StageCoachRead(BaseModel):
    explanation: str
    actions: list[StageCoachActionRead]
    model_id: str
    prompt_version: str

@router.post("/stage/coach", response_model=StageCoachRead)
def stage_coach(
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Explain authoritative stage conditions; this endpoint cannot change project state."""
    require_ai_on(project)
    options = stage_machine.next_options(db, project)
    evaluated = [(option, ec.conditions(db, project, option.stage)) for option in options]
    context = {
        "stage": project.stage.value,
        "research_type": project.research_type,
        "next": [{"stage": option.stage.value, "gate": option.gate.value if option.gate else None, "ready": option.ready}
                 for option, _ in evaluated],
        "conditions": [_condition_payload(condition) for _, conditions in evaluated for condition in conditions],
    }
    try:
        result = coach(llm_for_project(get_settings(), project, purpose="stage_coach"), context)
    except (AIOffError, LLMConfigurationError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LLMResponseError, StageCoachError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    audit.record(
        db, actor=audit.user_actor(user), action="stage.coach_generated", project_id=project.id,
        payload={"stage": project.stage.value, "condition_count": len(context["conditions"]),
                 "action_count": len(result.actions)},
        model_id=result.model_id, prompt_version=result.prompt_version,
    )
    db.commit()
    return StageCoachRead(explanation=result.explanation, actions=result.actions,
                          model_id=result.model_id, prompt_version=result.prompt_version)


@router.get("/stage/conditions", response_model=list[ConditionRead])
def get_stage_conditions(
    target: ProjectStage = Query(...),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Every exit condition for moving from the current stage to `target`. Read roles, like GET /stage."""
    rows = _condition_rows(db, project, target)
    db.commit()  # ensure_gates may create missing gate rows
    return rows


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
            db, project, payload.stage if payload else None, actor=audit.user_actor(user),
            override_reasons=payload.override_reasons if payload else None,
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
    except stage_machine.ConditionsUnmet as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "required": [_condition_payload(c) for c in exc.required],
                "recommended": [_condition_payload(c) for c in exc.recommended],
            },
        ) from exc
    except stage_machine.ConditionOverrideInvalid as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except art.StageError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return AdvanceRead(from_stage=result.from_stage, to_stage=result.to_stage, gate=result.gate)
