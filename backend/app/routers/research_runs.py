from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.executor import execute_research_run
from app.config import get_settings
from app.database import get_db
from app.dependencies import owned_project
from app.models import Project, ResearchRun, ResearchRunStatus
from app.schemas import ResearchRunCreate, ResearchRunRead

router = APIRouter(prefix="/projects/{project_id}/research-runs", tags=["research runs"])

MAX_RETRY_ATTEMPTS = 3


def _get_owned_run(run_id: uuid.UUID, project: Project, db: Session) -> ResearchRun:
    run = db.scalar(select(ResearchRun).where(ResearchRun.id == run_id, ResearchRun.project_id == project.id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found")
    return run


@router.get("", response_model=list[ResearchRunRead])
def list_research_runs(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(
        select(ResearchRun).where(ResearchRun.project_id == project.id).order_by(ResearchRun.created_at.desc()).limit(30)
    ).all()


@router.post("", response_model=ResearchRunRead, status_code=status.HTTP_202_ACCEPTED)
def create_research_run(
    payload: ResearchRunCreate,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    recent_count = db.scalar(
        select(func.count(ResearchRun.id)).where(ResearchRun.project_id == project.id, ResearchRun.created_at >= since)
    ) or 0
    if recent_count >= settings.research_run_limit_per_day:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This project has reached its daily research-run limit. Try again later or raise the configured limit.",
        )

    run = ResearchRun(project_id=project.id, question=payload.question.strip(), status=ResearchRunStatus.queued)
    project.updated_at = datetime.now(timezone.utc)
    db.add(run)
    db.commit()
    db.refresh(run)

    # Inline execution is deliberately only a local-development convenience. Production uses app.worker.
    if settings.run_research_inline:
        execute_research_run(str(run.id))
        db.refresh(run)
    return run


@router.get("/{run_id}", response_model=ResearchRunRead)
def get_research_run(
    run_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    return _get_owned_run(run_id, project, db)


@router.post("/{run_id}/retry", response_model=ResearchRunRead, status_code=status.HTTP_202_ACCEPTED)
def retry_research_run(
    run_id: uuid.UUID,
    project: Project = Depends(owned_project),
    db: Session = Depends(get_db),
):
    run = _get_owned_run(run_id, project, db)
    if run.status not in {ResearchRunStatus.failed, ResearchRunStatus.needs_configuration}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed or needs_configuration runs can be retried.",
        )
    if run.attempt_count >= MAX_RETRY_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run has already been attempted {run.attempt_count} times (max {MAX_RETRY_ATTEMPTS}).",
        )
    run.status = ResearchRunStatus.queued
    run.error_message = None
    run.answer = None
    run.completed_at = None
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    settings = get_settings()
    if settings.run_research_inline:
        execute_research_run(str(run.id))
        db.refresh(run)
    return run



