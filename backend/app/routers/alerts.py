"""Saved-query alerts (plan M1.12, spec 5.2.4): subscribe a saved search to scheduled re-runs.

A re-run is a `query_alert` task that needs gate G2, like a queued search run (M1.8.1): the person
approved the query once, and that approval covers its scheduled re-runs. `POST …/run` triggers a
re-run immediately (same task, so the same gate). Nothing here approves, releases or runs anything.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, SearchAlert, SearchQuery, Task, TaskStatus, User, utcnow
from datetime import datetime as dt_datetime
from app.query_alerts import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    NEW_RESULTS_CAP,
    QUERY_ALERT,
    AlertError,
    create_alert,
    latest_search,
    new_records,
)
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}", tags=["alerts"])

_OPEN = {TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused}


class AlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: int | None = Field(
        default=None, ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS,
        description="How often to re-run the search (1 hour to 30 days; default 1 day).",
    )


class AlertRunQueued(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str  # "blocked" until gate G2 is approved by a person, else "queued"
    blocked_by_gate: str | None = None


class AlertRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str
    search_id: str
    interval_seconds: int
    enabled: bool
    next_run_at: dt_datetime
    last_run_at: dt_datetime | None
    last_run_version: int | None
    last_new: int | None


def _read(alert: SearchAlert, *, database: str | None = None, query_string: str | None = None,
          latest_version: int | None = None, last_new_results: list[dict] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "alert_id": str(alert.id),
        "search_id": str(alert.search_id),
        "interval_seconds": alert.interval_seconds,
        "enabled": alert.enabled,
        "next_run_at": alert.next_run_at,
        "last_run_at": alert.last_run_at,
        "last_run_version": alert.last_run_version,
        "last_new": alert.last_new,
    }
    if last_new_results is not None:
        out.update(database=database, query_string=query_string, latest_version=latest_version,
                   last_new_results=last_new_results)
    return out


def _open_alert_tasks(db: Session, project: Project, alert_id: uuid.UUID) -> list[Task]:
    return [
        t for t in db.scalars(select(Task).where(Task.project_id == project.id, Task.type == QUERY_ALERT))
        if t.status in _OPEN and (t.payload or {}).get("alert_id") == str(alert_id)
    ]


@router.post("/searches/{search_id}/alerts", response_model=AlertRead, status_code=status.HTTP_201_CREATED)
def subscribe(
    search_id: uuid.UUID,
    body: AlertCreate | None = None,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Watch a saved search: re-run it on a schedule and surface papers new since the earlier runs."""
    if latest_search(db, project, search_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search not found")
    actor = audit.user_actor(user)
    try:
        alert = create_alert(
            db, project, search_id,
            interval_seconds=(body.interval_seconds if body else None) or DEFAULT_INTERVAL_SECONDS, actor=actor,
        )
    except AlertError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return _read(alert)


@router.get("/alerts")
def list_alerts(
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """All of this project's alert subscriptions, with what the last completed run surfaced."""
    alerts = db.scalars(select(SearchAlert).where(
        SearchAlert.project_id == project.id).order_by(SearchAlert.created_at, SearchAlert.id)).all()
    out = []
    for alert in alerts:
        latest = latest_search(db, project, alert.search_id)
        last_new_results = (
            new_records(db, project, alert.search_id, alert.last_run_version)[:NEW_RESULTS_CAP]
            if alert.last_run_version is not None else []
        )
        out.append(_read(
            alert,
            database=latest.database if latest else None,
            query_string=latest.query_string if latest else None,
            latest_version=latest.version if latest else None,
            last_new_results=last_new_results,
        ))
    return out


@router.post("/alerts/{alert_id}/run", response_model=AlertRunQueued, status_code=status.HTTP_202_ACCEPTED)
def run_now(
    alert_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Re-run one alert's search immediately (the same G2-gated task the schedule enqueues)."""
    alert = db.scalar(select(SearchAlert).where(
        SearchAlert.id == alert_id, SearchAlert.project_id == project.id))
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if _open_alert_tasks(db, project, alert_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This alert already has a run waiting or in progress")
    actor = audit.user_actor(user)
    now = utcnow()
    task = enqueue_task(
        db, project.id, QUERY_ALERT,
        {"project_id": str(project.id), "alert_id": str(alert.id), "enqueued_at": now.isoformat(), "actor": actor},
        actor=actor,
        idempotency_key=f"query-alert-manual:{alert.id}:{now.isoformat()}",
    )
    audit.record(
        db, actor=actor, action="query_alert.run_requested", project_id=project.id,
        payload={"task_id": str(task.id), "alert_id": str(alert.id),
                 "blocked_by_gate": task.blocked_by_gate.value if task.blocked_by_gate else None},
    )
    db.commit()
    return AlertRunQueued(
        task_id=str(task.id), status=task.status.value,
        blocked_by_gate=task.blocked_by_gate.value if task.blocked_by_gate else None,
    )


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(
    alert_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    alert = db.scalar(select(SearchAlert).where(
        SearchAlert.id == alert_id, SearchAlert.project_id == project.id))
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    audit.record(
        db, actor=audit.user_actor(user), action="query_alert.deleted", project_id=project.id,
        payload={"alert_id": str(alert.id), "search_id": str(alert.search_id)},
    )
    db.delete(alert)
    db.commit()
