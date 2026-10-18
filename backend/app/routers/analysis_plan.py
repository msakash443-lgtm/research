"""Analysis plans and generated scripts (M5.3.1-M5.3.2, spec 5.9).

Plans are created against one intact dataset version. Lock is owner-only and freezes the plan; once G8 is approved
no plan can be created or locked, matching the meta-analysis rule. Nothing here runs a script or calls a model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analysis_plan as rules
from app import analysis_runner, audit, datasets
from app.config import get_settings
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.gates import gate_is_approved
from app.models import AnalysisPlan, AnalysisRun, DatasetVersion, GateCode, Project, User
from app.models import utcnow

router = APIRouter(prefix="/projects/{project_id}", tags=["analysis-plans"])


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tests: list[Literal["descriptives", "correlations"]] = Field(min_length=1, max_length=2)
    variables: list[str] = Field(min_length=1, max_length=rules.MAX_VARIABLES)
    seed: int = Field(ge=0, le=rules.MAX_SEED)


def _dataset(db: Session, project: Project, dataset_id: uuid.UUID) -> DatasetVersion:
    version = db.get(DatasetVersion, dataset_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset version not found in this project.")
    return version


def _plan(db: Session, project: Project, plan_id: uuid.UUID) -> AnalysisPlan:
    row = db.scalar(select(AnalysisPlan).where(AnalysisPlan.id == plan_id, AnalysisPlan.project_id == project.id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis plan not found in this project.")
    return row


def _plan_intact(row: AnalysisPlan, dataset: DatasetVersion) -> bool:
    return rules.plan_hash(row.spec_json, dataset.sha256) == row.plan_sha256


def _read(row: AnalysisPlan, dataset: DatasetVersion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "dataset_id": str(row.dataset_id),
        "dataset_sha256": dataset.sha256,
        "dataset_intact": datasets.sha256_hex(dataset.content) == dataset.sha256,
        "spec": row.spec_json,
        "plan_sha256": row.plan_sha256,
        "environment_sha256": row.environment_sha256,
        "plan_intact": _plan_intact(row, dataset),
        "locked": row.locked_at is not None,
        "locked_at": row.locked_at.isoformat() if isinstance(row.locked_at, datetime) else None,
        "locked_by": row.locked_by,
        "created_by": row.created_by,
    }


@router.post("/datasets/{dataset_id}/analysis-plans", status_code=status.HTTP_201_CREATED)
def create_plan(
    dataset_id: uuid.UUID,
    body: PlanIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    db.refresh(project, with_for_update=True)
    if gate_is_approved(db, project.id, GateCode.G8):
        raise HTTPException(status.HTTP_409_CONFLICT, "G8 has locked the analysis plan")
    dataset = _dataset(db, project, dataset_id)
    if datasets.sha256_hex(dataset.content) != dataset.sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset no longer matches its hash, so no plan can use it.")
    try:
        spec = rules.validate(body.model_dump(), dataset.column_names)
    except rules.PlanError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    row = AnalysisPlan(
        project_id=project.id,
        dataset_id=dataset.id,
        spec_json=spec,
        plan_sha256=rules.plan_hash(spec, dataset.sha256),
        environment_sha256=rules.environment_hash(),
        created_by=str(user.id),
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="analysis_plan.created", project_id=project.id,
        payload={"plan_id": str(row.id), "dataset_id": str(dataset.id), "plan_sha256": row.plan_sha256},
    )
    db.commit()
    db.refresh(row)
    return _read(row, dataset)


@router.get("/analysis-plans")
def list_plans(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AnalysisPlan).where(AnalysisPlan.project_id == project.id).order_by(AnalysisPlan.created_at.asc())
    ).all()
    return [_read(row, db.get(DatasetVersion, row.dataset_id)) for row in rows]


@router.get("/analysis-plans/{analysis_plan_id}")
def get_plan(
    analysis_plan_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = _plan(db, project, analysis_plan_id)
    return _read(row, db.get(DatasetVersion, row.dataset_id))


@router.get("/analysis-plans/{analysis_plan_id}/script")
def get_script(
    analysis_plan_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = _plan(db, project, analysis_plan_id)
    dataset = db.get(DatasetVersion, row.dataset_id)
    if datasets.sha256_hex(dataset.content) != dataset.sha256 or not _plan_intact(row, dataset):
        raise HTTPException(status.HTTP_409_CONFLICT, "The plan or its dataset no longer matches its hash; no script is generated.")
    script = rules.generate_script(row.spec_json, dataset.name, dataset.sha256)
    return {"plan_id": str(row.id), "plan_sha256": row.plan_sha256, "filename": f"analysis_{str(row.id)[:8]}.py", "script": script}


@router.post("/analysis-plans/{analysis_plan_id}/lock")
def lock_plan(
    analysis_plan_id: uuid.UUID,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    db.refresh(project, with_for_update=True)
    row = db.scalar(
        select(AnalysisPlan).where(AnalysisPlan.id == analysis_plan_id, AnalysisPlan.project_id == project.id).with_for_update()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis plan not found in this project.")
    dataset = db.get(DatasetVersion, row.dataset_id)
    if gate_is_approved(db, project.id, GateCode.G8):
        raise HTTPException(status.HTTP_409_CONFLICT, "G8 has locked the analysis plan")
    if row.locked_at is not None:
        return _read(row, dataset)  # idempotent: locking twice changes nothing
    if datasets.sha256_hex(dataset.content) != dataset.sha256 or not _plan_intact(row, dataset):
        raise HTTPException(status.HTTP_409_CONFLICT, "The plan or its dataset no longer matches its hash; it cannot be locked.")
    row.locked_at = utcnow()
    row.locked_by = audit.user_actor(user)
    audit.record(
        db, actor=audit.user_actor(user), action="analysis_plan.locked", project_id=project.id,
        payload={"plan_id": str(row.id), "plan_sha256": row.plan_sha256},
    )
    db.commit()
    db.refresh(row)
    return _read(row, dataset)


def _run_read(row: AnalysisRun) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "analysis_plan_id": str(row.analysis_plan_id),
        "dataset_id": str(row.dataset_id),
        "plan_sha256": row.plan_sha256,
        "dataset_sha256": row.dataset_sha256,
        "script_sha256": row.script_sha256,
        "environment_sha256": row.environment_sha256,
        "image": row.image,
        "output_sha256": row.output_sha256,
        "outputs": row.output_json,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }


@router.post("/analysis-plans/{analysis_plan_id}/runs", status_code=status.HTTP_201_CREATED)
def create_run(
    analysis_plan_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run a locked plan in the Docker sandbox over its dataset (M5.3.3). Refused until the plan is locked."""
    row = _plan(db, project, analysis_plan_id)
    if row.locked_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Lock the analysis plan before running it.")
    dataset = db.get(DatasetVersion, row.dataset_id)
    if datasets.sha256_hex(dataset.content) != dataset.sha256 or not _plan_intact(row, dataset):
        raise HTTPException(status.HTTP_409_CONFLICT, "The plan or its dataset no longer matches its hash; nothing was run.")
    settings = get_settings()
    try:
        image = analysis_runner.check_image(settings.analysis_sandbox_image or "")
    except analysis_runner.RunnerError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    script = rules.generate_script(row.spec_json, dataset.name, dataset.sha256)
    try:
        result = analysis_runner.run_script(
            script, dataset.content, dataset_sha256=dataset.sha256, plan_sha256=row.plan_sha256,
            image=image, timeout=settings.analysis_sandbox_timeout_seconds,
        )
    except analysis_runner.RunnerError as exc:
        audit.record(db, actor=audit.user_actor(user), action="analysis.run_failed", project_id=project.id,
                     payload={"plan_id": str(row.id), "reason": str(exc)[:300]})
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run = AnalysisRun(
        project_id=project.id,
        analysis_plan_id=row.id,
        dataset_id=dataset.id,
        plan_sha256=row.plan_sha256,
        dataset_sha256=dataset.sha256,
        script_sha256=result["script_sha256"],
        environment_sha256=row.environment_sha256,
        image=result["image"],
        output_json=result["outputs"],
        output_sha256=result["output_sha256"],
        created_by=str(user.id),
    )
    db.add(run)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="analysis.run_completed", project_id=project.id,
                 payload={"run_id": str(run.id), "plan_id": str(row.id), "output_sha256": run.output_sha256})
    db.commit()
    db.refresh(run)
    return _run_read(run)


@router.get("/analysis-plans/{analysis_plan_id}/runs")
def list_runs(
    analysis_plan_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    row = _plan(db, project, analysis_plan_id)
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.analysis_plan_id == row.id).order_by(AnalysisRun.created_at)).all()
    return [_run_read(run) for run in runs]

