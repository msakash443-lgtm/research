from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, ResearchRun, ResearchRunStatus, User, utcnow
from app.schemas import ResearchRunCreate, ResearchRunRead
from app.task_queue import enqueue_task
from app.task_runner import run_one_task

router = APIRouter(prefix="/projects/{project_id}/research-runs", tags=["research runs"])


@router.get("", response_model=list[ResearchRunRead])
def list_research_runs(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(
        select(ResearchRun).where(ResearchRun.project_id == project.id).order_by(ResearchRun.created_at.desc()).limit(30)
    ).all()


@router.post("", response_model=ResearchRunRead, status_code=status.HTTP_202_ACCEPTED)
def create_research_run(
    payload: ResearchRunCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if payload.use_web_retrieval and not settings.arc_retrieval_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Web retrieval is not enabled on this deployment.",
        )
    since = utcnow() - timedelta(days=1)
    recent_count = db.scalar(
        select(func.count(ResearchRun.id)).where(ResearchRun.project_id == project.id, ResearchRun.created_at >= since)
    ) or 0
    if recent_count >= settings.research_run_limit_per_day:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This project has reached its daily research-run limit. Try again later or raise the configured limit.",
        )

    run = ResearchRun(
        project_id=project.id,
        question=payload.question.strip(),
        status=ResearchRunStatus.queued,
        use_web_retrieval=payload.use_web_retrieval,
        created_by=audit.user_actor(user),
    )
    project.touch()
    db.add(run)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="research_run.queued", project_id=project.id,
        payload={"run_id": str(run.id), "use_web_retrieval": run.use_web_retrieval},
    )
    # The run and its task are created together, so neither exists alone.
    task = enqueue_task(
        db, project.id, "research_run", {"run_id": str(run.id)}, actor=audit.user_actor(user),
        idempotency_key=f"research_run:{run.id}",
    )
    db.commit()
    db.refresh(run)

    # Inline mode is a local-development convenience: run *this* task now, in the request, through the
    # same queue and handler a worker uses (so retries, failures and gates behave identically).
    # Production uses app.worker. A failing run comes back as a normal `failed` run, never a 500.
    if settings.run_research_inline:
        run_one_task(task_id=task.id)
        db.refresh(run)
    return run
