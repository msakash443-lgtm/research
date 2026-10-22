from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.gates import gate_is_approved
from app.models import GateCode, Project, Submission, SubmissionReminder, User, utcnow
from app.schemas import SubmissionCreate, SubmissionRead, SubmissionTransition, SubmissionReminderCreate, SubmissionReminderRead
from app.submissions import integration_enabled, transition
from app.task_handlers import REMINDER_DISPATCH
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}/submissions", tags=["submissions"])


@router.get("", response_model=list[SubmissionRead])
def list_submissions(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(Submission).where(Submission.project_id == project.id).order_by(Submission.created_at.desc())).all()

@router.post("/integrations/{provider}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def request_integration(
    provider: str,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if provider not in {"orcid", "preprint", "zenodo", "osf"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown publication integration")
    settings = get_settings()
    if not integration_enabled(settings.model_dump(), provider):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{provider} integration is disabled")
    actor = audit.user_actor(user)
    audit.record(db, actor=actor, action="submission.integration_requested", project_id=project.id,
                 payload={"provider": provider, "status": "adapter_unavailable"})
    db.commit()
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=f"{provider} integration adapter is not available")


@router.post("", response_model=SubmissionRead, status_code=status.HTTP_201_CREATED)
def create_submission(
    body: SubmissionCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = body.model_dump(mode="json")
    if data.get("manuscript_url"):
        data["manuscript_url"] = str(body.manuscript_url)
    row = Submission(project_id=project.id, created_by=audit.user_actor(user), **data)
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="submission.created", project_id=project.id,
                 payload={"submission_id": str(row.id), "status": row.status})
    db.commit()
    db.refresh(row)
    return row


@router.post("/{submission_id}/transition", response_model=SubmissionRead)
def transition_submission(
    submission_id: uuid.UUID,
    body: SubmissionTransition,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Submission).where(Submission.id == submission_id, Submission.project_id == project.id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    try:
        target = transition(row.status, body.status)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if target == "submitted" and not gate_is_approved(db, project.id, GateCode.G11):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="G11 must be approved by the project owner before submission")
    previous = row.status
    row.status = target
    if target == "submitted":
        row.submitted_at = utcnow()
    actor = audit.user_actor(user)
    audit.record(db, actor=actor, action="submission.transitioned", project_id=project.id,
                 payload={"submission_id": str(row.id), "from": previous, "to": target})
    db.commit()
    db.refresh(row)
    return row

@router.get("/reminders", response_model=list[SubmissionReminderRead])
def list_reminders(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(SubmissionReminder).where(SubmissionReminder.project_id == project.id).order_by(SubmissionReminder.due_at)).all()


@router.post("/reminders", response_model=SubmissionReminderRead, status_code=status.HTTP_201_CREATED)
def create_reminder(
    body: SubmissionReminderCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Submission.id).where(Submission.id == body.submission_id, Submission.project_id == project.id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    row = SubmissionReminder(project_id=project.id, created_by=audit.user_actor(user), **body.model_dump())
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="submission.reminder_created", project_id=project.id,
                 payload={"reminder_id": str(row.id), "submission_id": str(row.submission_id)})
    db.commit()
    db.refresh(row)
    return row

@router.post("/reminders/dispatch", status_code=status.HTTP_202_ACCEPTED)
def queue_reminder_dispatch(
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    actor = audit.user_actor(user)
    task = enqueue_task(
        db, project.id, REMINDER_DISPATCH,
        payload={"project_id": str(project.id), "requested_by": actor},
        actor=actor, idempotency_key=f"submission-reminders:{project.id}:{utcnow().date().isoformat()}",
    )
    audit.record(db, actor=actor, action="submission.reminders_dispatch_requested", project_id=project.id,
                 payload={"task_id": str(task.id)})
    db.commit()
    return {"task_id": task.id, "status": task.status.value}
