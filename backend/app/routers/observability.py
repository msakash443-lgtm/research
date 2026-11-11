from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import MANAGE_ROLES, project_access
from app.models import Project
from app.observability import project_observability

router = APIRouter(prefix="/projects/{project_id}/observability", tags=["observability"])


class StepRow(BaseModel):
    type: str
    total: int
    completed: int
    failed: int
    queued: int
    running: int
    paused: int
    blocked: int
    retried: int  # ran more than once (a retry after a failed attempt)


class StepDurationRow(BaseModel):
    type: str
    timed: int  # completed steps that recorded a start and a finish
    avg_seconds: float | None  # final attempt only; None when nothing is timed
    max_seconds: float | None


class ModelCallRow(BaseModel):
    purpose: str
    model: str
    calls: int
    tokens: int
    calls_without_token_counts: int
    cost_usd: float | None  # priced calls only; None when no call in the row has a price (app/pricing.py)
    calls_unpriced: int  # calls with no price for their model, or a missing token count


class FailureRow(BaseModel):
    id: str
    type: str
    finished_at: datetime | None  # when the step ended for good
    run_id: str | None = None  # research runs only: the run this step belongs to (Ask step link)
    attempts: int


class ObservabilityRead(BaseModel):
    steps: list[StepRow]
    step_durations: list[StepDurationRow]
    steps_total: int
    steps_failed: int
    steps_retried: int
    model_calls: list[ModelCallRow]
    model_calls_total: int
    tokens_total: int
    cost_usd_total: float  # sum over priced calls only: a lower bound when calls_unpriced_total > 0
    calls_unpriced_total: int
    failures: list[FailureRow]  # newest first, at most 20; no error text


@router.get("", response_model=ObservabilityRead)
def project_metrics(
    since: datetime | None = Query(default=None, description="Count only records created at or after this time (a time without an offset is UTC)."),
    until: datetime | None = Query(default=None, description="Count only records created before this time (exclusive; a time without an offset is UTC)."),
    project: Project = Depends(project_access(MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    """Per-step outcomes and model-call token counts for this project. Counts and names only, no error text.

    Owner only (X.136): cost and call figures are not shown to co-authors, reviewers or supervisors.
    `since` and `until` (X.137) limit every figure to records created in [since, until).
    """
    since, until = _as_utc(since), _as_utc(until)
    if since is not None and until is not None and since >= until:
        raise HTTPException(status_code=422, detail="since must be earlier than until")
    return project_observability(db, project.id, since, until)


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(timezone.utc)
    except OverflowError:  # e.g. 0001-01-01T00:00:00+05:00 falls before year 1 in UTC
        raise HTTPException(status_code=422, detail="time is out of range") from None
