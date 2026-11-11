from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, review_comments
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.lookups import project_document_or_404
from app.models import Document, Project, ReviewComment, ReviewIssue, User

router = APIRouter(prefix="/projects/{project_id}/reviews", tags=["reviews"])


class ReviewCommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    persona: str = Field(min_length=1, max_length=60)
    severity: str = Field(min_length=1, max_length=12)
    body: str = Field(min_length=1, max_length=4000)


class ReviewCommentRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    persona: str
    severity: str
    status: str
    body: str
    created_by: str
    created_at: datetime


@router.get("", response_model=list[ReviewCommentRead])
def list_review_comments(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(ReviewComment).where(ReviewComment.project_id == project.id).order_by(ReviewComment.created_at, ReviewComment.id)).all()


@router.post("", response_model=ReviewCommentRead, status_code=status.HTTP_201_CREATED)
def create_review_comment(
    body: ReviewCommentCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project_document_or_404(db, project, body.document_id)
    try:
        severity, comment_status, text = review_comments.validate_comment(severity=body.severity, status="open", body=body.body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    row = ReviewComment(project_id=project.id, document_id=body.document_id, persona=body.persona.strip(), severity=severity,
                        status=comment_status, body=text, created_by=audit.user_actor(user))
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="review.comment_created", project_id=project.id,
                 payload={"comment_id": str(row.id), "document_id": str(row.document_id), "severity": row.severity})
    db.commit()
    db.refresh(row)
    return row
class ReviewIssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    severity: str = Field(min_length=1, max_length=12)
    assignee: str | None = Field(default=None, max_length=100)
    redline: dict | None = None


class ReviewIssueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = None
    assignee: str | None = Field(default=None, max_length=100)
    response_text: str | None = Field(default=None, max_length=10000)
    redline: dict | None = None


class ReviewIssueRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    title: str
    severity: str
    status: str
    assignee: str | None
    response_text: str | None
    redline_json: dict | None
    created_by: str
    created_at: datetime


@router.get("/issues", response_model=list[ReviewIssueRead])
def list_review_issues(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return db.scalars(select(ReviewIssue).where(ReviewIssue.project_id == project.id).order_by(ReviewIssue.created_at, ReviewIssue.id)).all()


@router.post("/issues", response_model=ReviewIssueRead, status_code=status.HTTP_201_CREATED)
def create_review_issue(
    body: ReviewIssueCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project_document_or_404(db, project, body.document_id)
    if body.severity not in {"info", "minor", "major", "blocking"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown issue severity")
    row = ReviewIssue(project_id=project.id, document_id=body.document_id, title=body.title.strip(),
                      severity=body.severity, assignee=body.assignee, redline_json=body.redline,
                      created_by=audit.user_actor(user))
    db.add(row)
    db.flush()
    audit.record(db, actor=audit.user_actor(user), action="review.issue_created", project_id=project.id,
                 payload={"issue_id": str(row.id), "document_id": str(row.document_id), "severity": row.severity})
    db.commit()
    db.refresh(row)
    return row


@router.patch("/issues/{issue_id}", response_model=ReviewIssueRead)
def update_review_issue(
    issue_id: uuid.UUID,
    body: ReviewIssueUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(ReviewIssue).where(ReviewIssue.id == issue_id, ReviewIssue.project_id == project.id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review issue not found")
    if body.status is not None and body.status not in {"open", "accepted", "resolved", "rejected"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown issue status")
    for field in ("status", "assignee", "response_text"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if body.redline is not None:
        row.redline_json = body.redline
    audit.record(db, actor=audit.user_actor(user), action="review.issue_updated", project_id=project.id,
                 payload={"issue_id": str(row.id), "status": row.status})
    db.commit()
    db.refresh(row)
    return row
