"""Snowballing: queue a citation-chasing run, then list what ran (spec 5.2.3, plan M1.9).

A run is never done inside the request. `POST` queues a `snowball_run` task; that task type needs
gate G2, so it starts **blocked** until a person approves G2 (M1.8.1). Nothing here approves,
releases or runs anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.connectors.access import is_enabled
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, SeedPaper, SnowballRun, Source, Task, TaskStatus, User
from app.screening import screening_locked
from app.snowball import CONNECTORS, MAX_NEW, MAX_PER_PAPER, MAX_ROUNDS, MAX_STARTS
from app.task_handlers import SNOWBALL_RUN
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}/snowball", tags=["snowball"])

_OPEN = {TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused}


class SnowballCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: Literal["openalex", "semantic_scholar", "pubmed", "opencitations"]
    directions: list[Literal["backward", "forward"]] = Field(default_factory=lambda: ["backward", "forward"], min_length=1, max_length=2)
    rounds: int = Field(default=1, ge=1, le=MAX_ROUNDS)
    max_per_paper: int = Field(default=50, ge=1, le=MAX_PER_PAPER)
    max_new: int = Field(default=200, ge=1, le=MAX_NEW)
    include_seeds: bool = True
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_STARTS)


class SnowballQueued(BaseModel):
    task_id: uuid.UUID
    run_id: uuid.UUID
    status: str  # "blocked" until gate G2 is approved by a person, else "queued"
    blocked_by_gate: str | None = None


class SnowballRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connector: str
    directions: list
    rounds: int
    caps: dict
    starts: list
    counts: dict
    run_at: datetime
    created_by: str | None


@router.post("", response_model=SnowballQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_snowball(
    body: SnowballCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if body.connector not in CONNECTORS or not is_enabled(body.connector, settings.connectors_enabled, settings.connectors_allow_scraping):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The '{body.connector}' connector is not enabled on this server")
    if screening_locked(db, project):
        raise HTTPException(status.HTTP_409_CONFLICT, "Gate G3 is approved, so no new records can enter screening. Reopen the screening stage first.")
    source_ids = list(dict.fromkeys(body.source_ids))
    if source_ids:
        found = set(db.scalars(select(Source.id).where(Source.project_id == project.id, Source.id.in_(source_ids), Source.merged_into.is_(None))))
        missing = [str(s) for s in source_ids if s not in found]
        if missing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source(s) not in this project: {', '.join(missing)}")
    seeds = db.scalars(select(SeedPaper.id).where(SeedPaper.project_id == project.id)).all() if body.include_seeds else []
    if not source_ids and not seeds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing to start from: add seed papers or choose sources")
    if len(source_ids) + len(seeds) > MAX_STARTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"At most {MAX_STARTS} start papers per run")

    actor = audit.user_actor(user)
    run_id = uuid.uuid4()
    payload = {
        "project_id": str(project.id), "run_id": str(run_id), "connector": body.connector, "directions": list(dict.fromkeys(body.directions)),
        "rounds": body.rounds, "max_per_paper": body.max_per_paper, "max_new": body.max_new,
        "include_seeds": body.include_seeds, "source_ids": [str(s) for s in source_ids], "actor": actor,
    }
    task = enqueue_task(db, project.id, SNOWBALL_RUN, payload=payload, actor=actor)
    audit.record(
        db, actor=actor, action="snowball.requested", project_id=project.id,
        payload={"task_id": str(task.id), "run_id": str(run_id), "connector": body.connector, "directions": payload["directions"],
                 "rounds": body.rounds, "starts": len(source_ids) + len(seeds)},
    )
    db.commit()
    return SnowballQueued(
        task_id=task.id, run_id=run_id, status=task.status.value,
        blocked_by_gate=task.blocked_by_gate.value if task.blocked_by_gate else None,
    )


@router.get("")
def list_snowball_runs(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    runs = db.scalars(select(SnowballRun).where(SnowballRun.project_id == project.id).order_by(SnowballRun.run_at, SnowballRun.id)).all()
    pending = [
        {
            "task_id": str(t.id), "run_id": (t.payload or {}).get("run_id"), "connector": (t.payload or {}).get("connector"),
            "status": t.status.value, "blocked_by_gate": t.blocked_by_gate.value if t.blocked_by_gate else None,
        }
        for t in db.scalars(select(Task).where(Task.project_id == project.id, Task.type == SNOWBALL_RUN).order_by(Task.created_at))
        if t.status in _OPEN
    ]
    return {"runs": [SnowballRunRead.model_validate(r).model_dump(mode="json") for r in runs], "pending": pending}
