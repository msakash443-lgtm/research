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
from app import analysis_runner, audit, datasets, deviation_log, journal_tables, notebook_export, robustness
from app import robustness_suite as suite
from app.config import get_settings
from app.database import get_db
from app.dependencies import MANAGE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.gates import gate_is_approved
from app.models import AnalysisPlan, AnalysisRun, DatasetVersion, DeviationLog, GateCode, Project, RobustnessCheck, User
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


def _run(db: Session, project: Project, analysis_plan_id: uuid.UUID, run_id: uuid.UUID) -> AnalysisRun:
    row = db.scalar(
        select(AnalysisRun).where(AnalysisRun.id == run_id, AnalysisRun.analysis_plan_id == analysis_plan_id,
                                   AnalysisRun.project_id == project.id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found for this plan.")
    return row


def _robustness_read(row: RobustnessCheck) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "base_run_id": str(row.base_run_id),
        "kind": row.kind,
        "label": row.label,
        "compare_dataset_id": str(row.compare_dataset_id) if row.compare_dataset_id else None,
        "compare_plan_id": str(row.compare_plan_id) if row.compare_plan_id else None,
        "compare_run_id": str(row.compare_run_id) if row.compare_run_id else None,
        "base_fingerprint": row.base_fingerprint,
        "compare_fingerprint": row.compare_fingerprint,
        "identical": row.identical,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }


def _run_variant(
    db: Session, project: Project, user: User, settings: Any, dataset: DatasetVersion, spec: dict[str, Any],
) -> tuple[AnalysisPlan, AnalysisRun]:
    """Create, lock and run a new plan with `spec` against `dataset`, the same three steps as the single-purpose
    routes above but in one call. Raises `HTTPException` on any failure; nothing is left half-written because the
    caller commits once, after this returns."""
    plan = AnalysisPlan(
        project_id=project.id, dataset_id=dataset.id, spec_json=spec,
        plan_sha256=rules.plan_hash(spec, dataset.sha256), environment_sha256=rules.environment_hash(),
        created_by=str(user.id),
    )
    db.add(plan)
    db.flush()
    plan.locked_at = utcnow()
    plan.locked_by = audit.user_actor(user)
    audit.record(db, actor=audit.user_actor(user), action="analysis_plan.created", project_id=project.id,
                 payload={"plan_id": str(plan.id), "dataset_id": str(dataset.id), "plan_sha256": plan.plan_sha256})
    audit.record(db, actor=audit.user_actor(user), action="analysis_plan.locked", project_id=project.id,
                 payload={"plan_id": str(plan.id), "plan_sha256": plan.plan_sha256})
    try:
        image = analysis_runner.check_image(settings.analysis_sandbox_image or "")
    except analysis_runner.RunnerError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    script = rules.generate_script(spec, dataset.name, dataset.sha256)
    try:
        result = analysis_runner.run_script(
            script, dataset.content, dataset_sha256=dataset.sha256, plan_sha256=plan.plan_sha256,
            image=image, timeout=settings.analysis_sandbox_timeout_seconds,
        )
    except analysis_runner.RunnerError as exc:
        audit.record(db, actor=audit.user_actor(user), action="analysis.run_failed", project_id=project.id,
                     payload={"plan_id": str(plan.id), "reason": str(exc)[:300]})
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run = AnalysisRun(
        project_id=project.id, analysis_plan_id=plan.id, dataset_id=dataset.id,
        plan_sha256=plan.plan_sha256, dataset_sha256=dataset.sha256, script_sha256=result["script_sha256"],
        environment_sha256=plan.environment_sha256, image=result["image"], output_json=result["outputs"],
        output_sha256=result["output_sha256"], created_by=str(user.id),
    )
    db.add(run)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="analysis.run_completed", project_id=project.id,
                 payload={"run_id": str(run.id), "plan_id": str(plan.id), "output_sha256": run.output_sha256})
    return plan, run


class RobustnessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["rerun_identity", "alternative_spec", "outlier_sensitivity", "missing_data"]
    drop_variable: str | None = Field(default=None, max_length=200)
    compare_dataset_id: uuid.UUID | None = None


@router.post("/analysis-plans/{analysis_plan_id}/runs/{run_id}/robustness", status_code=status.HTTP_201_CREATED)
def create_robustness_check(
    analysis_plan_id: uuid.UUID,
    run_id: uuid.UUID,
    body: RobustnessIn,
    project: Project = Depends(project_access(MANAGE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Robustness suite over a stored run (M5.4.2, spec 5.9 bullet 3): re-run the same spec unchanged, a variant
    with one variable dropped, or the same spec against a different, already governed dataset version (e.g. one an
    approved exclusion rule produced). Each variant is its own locked plan and sandboxed run; the two outputs are
    compared by fingerprint (`app.robustness.fingerprint_results`), never by eye. Owner-only: it creates and locks
    new analysis plans, the same gate as `lock_plan`."""
    db.refresh(project, with_for_update=True)
    if gate_is_approved(db, project.id, GateCode.G8):
        raise HTTPException(status.HTTP_409_CONFLICT, "G8 has locked the analysis plan")
    base_plan = _plan(db, project, analysis_plan_id)
    base_run = _run(db, project, analysis_plan_id, run_id)
    base_dataset = db.get(DatasetVersion, base_run.dataset_id)
    settings = get_settings()

    if body.kind == "rerun_identity":
        compare_dataset, spec, label = base_dataset, base_plan.spec_json, "unchanged re-run"
    elif body.kind == "alternative_spec":
        if not body.drop_variable:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name the variable to drop.")
        label = f"drop {body.drop_variable}"
        alternatives = {alt["label"]: alt["spec"] for alt in suite.leave_one_out_specs(base_plan.spec_json)}
        if label not in alternatives:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                 f"{body.drop_variable!r} is not a variable this plan can drop and still run a test.")
        compare_dataset, spec = base_dataset, alternatives[label]
    else:
        if body.compare_dataset_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name the dataset version to compare against.")
        compare_dataset = _dataset(db, project, body.compare_dataset_id)
        if compare_dataset.id == base_dataset.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                 "Compare against a different dataset version than the run's own.")
        missing = [v for v in base_plan.spec_json["variables"] if v not in compare_dataset.column_names]
        if missing:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                 f"The comparison dataset is missing column(s): {', '.join(missing)}.")
        spec = base_plan.spec_json
        kind_label = "outlier" if body.kind == "outlier_sensitivity" else "missing-data"
        label = f"{kind_label} comparison: dataset {compare_dataset.id}"

    if datasets.sha256_hex(compare_dataset.content) != compare_dataset.sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "The comparison dataset no longer matches its hash.")
    compare_plan, compare_run = _run_variant(db, project, user, settings, compare_dataset, spec)

    base_fingerprint = robustness.fingerprint_results(base_run.output_json)
    compare_fingerprint = robustness.fingerprint_results(compare_run.output_json)
    check = RobustnessCheck(
        project_id=project.id, base_run_id=base_run.id, kind=body.kind, label=label,
        compare_dataset_id=compare_dataset.id, compare_plan_id=compare_plan.id, compare_run_id=compare_run.id,
        base_fingerprint=base_fingerprint, compare_fingerprint=compare_fingerprint,
        identical=base_fingerprint == compare_fingerprint, created_by=str(user.id),
    )
    db.add(check)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="robustness.checked", project_id=project.id,
                 payload={"check_id": str(check.id), "base_run_id": str(base_run.id), "kind": body.kind,
                          "identical": check.identical})
    db.commit()
    db.refresh(check)
    return _robustness_read(check)


@router.get("/analysis-plans/{analysis_plan_id}/runs/{run_id}/robustness")
def list_robustness_checks(
    analysis_plan_id: uuid.UUID,
    run_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    base_run = _run(db, project, analysis_plan_id, run_id)
    rows = db.scalars(
        select(RobustnessCheck).where(RobustnessCheck.base_run_id == base_run.id).order_by(RobustnessCheck.created_at.asc())
    ).all()
    return [_robustness_read(row) for row in rows]


@router.get("/analysis-plans/{analysis_plan_id}/runs/{run_id}/notebook")
def get_notebook(
    analysis_plan_id: uuid.UUID,
    run_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Reproducible notebook for a stored run (M5.4.2, spec 5.9 bullet 4). Nothing executes here; this packages
    the locked script, the dataset it ran over and the recorded output so the run can be reproduced outside the
    platform, at the same read visibility as the script and dataset-content routes above."""
    plan = _plan(db, project, analysis_plan_id)
    run = _run(db, project, analysis_plan_id, run_id)
    dataset = db.get(DatasetVersion, run.dataset_id)
    notebook = notebook_export.build_notebook(
        plan_id=str(plan.id), run_id=str(run.id), dataset_name=dataset.name, dataset_csv=dataset.content,
        plan_sha256=run.plan_sha256, dataset_sha256=run.dataset_sha256, environment_sha256=run.environment_sha256,
        image=run.image, script=rules.generate_script(plan.spec_json, dataset.name, dataset.sha256),
        output=run.output_json, output_sha256=run.output_sha256,
        run_created_at=run.created_at.isoformat() if isinstance(run.created_at, datetime) else "",
    )
    return {"plan_id": str(plan.id), "run_id": str(run.id),
            "filename": f"analysis_{str(run.id)[:8]}_notebook.ipynb", "notebook": notebook}



@router.get("/analysis-plans/{analysis_plan_id}/runs/{run_id}/tables")
def get_journal_tables(
    analysis_plan_id: uuid.UUID,
    run_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Journal-style tables for a stored run (M5.8.3). Values are copied from the recorded output and rounded for
    display only; nothing is recomputed or stored."""
    run = _run(db, project, analysis_plan_id, run_id)
    try:
        tables = journal_tables.build_tables(run.output_json)
    except journal_tables.TableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"run_id": str(run.id), "output_sha256": run.output_sha256, "tables": tables}


class DeviationLogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_run_id: uuid.UUID
    reasons: dict[str, str] = Field(default_factory=dict)


def _deviation_log_read(row: DeviationLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "planned_plan_id": str(row.planned_plan_id),
        "actual_run_id": str(row.actual_run_id),
        "planned_plan_sha256": row.planned_plan_sha256,
        "actual_plan_sha256": row.actual_plan_sha256,
        "planned_dataset_sha256": row.planned_dataset_sha256,
        "actual_dataset_sha256": row.actual_dataset_sha256,
        "deviations": row.log_json,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }


@router.post("/analysis-plans/{analysis_plan_id}/deviation-logs", status_code=status.HTTP_201_CREATED)
def create_deviation_log(
    analysis_plan_id: uuid.UUID,
    body: DeviationLogIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record the deviations between a locked plan (the pre-registered one) and a stored run of another plan (M5.8.4).
    Every deviation needs the researcher's reason; a missing or stray reason refuses the whole log, nothing is stored."""
    planned = _plan(db, project, analysis_plan_id)
    if planned.locked_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Lock the pre-registered analysis plan before logging deviations from it.")
    planned_dataset = db.get(DatasetVersion, planned.dataset_id)
    if datasets.sha256_hex(planned_dataset.content) != planned_dataset.sha256 or not _plan_intact(planned, planned_dataset):
        raise HTTPException(status.HTTP_409_CONFLICT, "The plan or its dataset no longer matches its hash; no log is recorded.")
    actual_run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == body.actual_run_id, AnalysisRun.project_id == project.id))
    if actual_run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found in this project.")
    actual_plan = db.get(AnalysisPlan, actual_run.analysis_plan_id)
    try:
        log = deviation_log.build_log(
            planned.spec_json, actual_plan.spec_json, planned_dataset.sha256, actual_run.dataset_sha256, body.reasons,
        )
    except deviation_log.DeviationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    row = DeviationLog(
        project_id=project.id,
        planned_plan_id=planned.id,
        actual_run_id=actual_run.id,
        planned_plan_sha256=planned.plan_sha256,
        actual_plan_sha256=actual_run.plan_sha256,
        planned_dataset_sha256=planned_dataset.sha256,
        actual_dataset_sha256=actual_run.dataset_sha256,
        log_json=[{"key": d.key, "kind": d.kind, "detail": d.detail, "reason": d.reason} for d in log],
        created_by=str(user.id),
    )
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="analysis.deviation_logged", project_id=project.id,
                 payload={"log_id": str(row.id), "plan_id": str(planned.id), "run_id": str(actual_run.id),
                          "deviation_count": len(log)})
    db.commit()
    db.refresh(row)
    return _deviation_log_read(row)


@router.get("/analysis-plans/{analysis_plan_id}/deviation-logs")
def list_deviation_logs(
    analysis_plan_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    row = _plan(db, project, analysis_plan_id)
    rows = db.scalars(
        select(DeviationLog).where(DeviationLog.planned_plan_id == row.id).order_by(DeviationLog.created_at.asc())
    ).all()
    return [_deviation_log_read(log) for log in rows]
