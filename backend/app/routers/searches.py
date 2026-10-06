"""Database searches: queue them, then list what ran (spec 5.2, gate G2).

A search is never run inside the request. `POST` queues a `search_run` task; that task type
needs gate G2, so it starts **blocked** and only a person approving G2 releases it (M0.5.4).
Nothing here approves, releases or runs anything.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.connectors.access import is_enabled
from app.connectors.factory import SEARCHABLE
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, SearchQuery, Task, TaskStatus, User
from app.search_query import BooleanQuery, ConceptBlock, QueryError
from app.search_query_adapters import adapt
from app.search_limits import DEFAULT_MAX_RESULTS, HARD_MAX_RESULTS
from app.task_handlers import SEARCH_RUN
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}/searches", tags=["searches"])

_OPEN = {TaskStatus.queued, TaskStatus.running, TaskStatus.blocked, TaskStatus.paused}


class SearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str = Field(max_length=50)
    blocks: list[ConceptBlock] = Field(min_length=1, max_length=10)
    filters: dict[str, str | int | bool] = Field(default_factory=dict, max_length=10)
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=HARD_MAX_RESULTS)


class SearchQueued(BaseModel):
    task_id: uuid.UUID
    search_id: uuid.UUID
    version: int
    status: str  # "blocked" until gate G2 is approved by a person, else "queued"
    blocked_by_gate: str | None = None


def _usable(database: str) -> None:
    settings = get_settings()
    if database not in SEARCHABLE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Searchable databases: {', '.join(SEARCHABLE)}")
    if not is_enabled(database, settings.connectors_enabled, settings.connectors_allow_scraping):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The '{database}' connector is not enabled on this server")


def _queued(db: Session, project: Project, user: User, payload: dict[str, Any]) -> SearchQueued:
    actor = audit.user_actor(user)
    task = enqueue_task(db, project.id, SEARCH_RUN, payload={**payload, "actor": actor}, actor=actor)
    audit.record(
        db, actor=actor, action="search.requested", project_id=project.id,
        payload={"task_id": str(task.id), "search_id": payload["search_id"], "version": payload["version"],
                 "database": payload["database"], "rerun": bool(payload.get("rerun"))},
    )
    db.commit()
    return SearchQueued(
        task_id=task.id, search_id=uuid.UUID(payload["search_id"]), version=payload["version"], status=task.status.value,
        blocked_by_gate=task.blocked_by_gate.value if task.blocked_by_gate else None,
    )


def _open_tasks(db: Session, project: Project) -> list[Task]:
    return [
        t for t in db.scalars(select(Task).where(Task.project_id == project.id, Task.type == SEARCH_RUN)) if t.status in _OPEN
    ]


@router.post("", response_model=SearchQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_search(
    body: SearchCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _usable(body.database)
    try:
        query = BooleanQuery(blocks=tuple(body.blocks))
        adapt(query, body.database)  # refuses a query the database can't take, now rather than after approval
    except QueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    payload = {
        "project_id": str(project.id), "search_id": str(uuid.uuid4()), "version": 1, "database": body.database,
        "blocks": [b.model_dump(mode="json") for b in body.blocks], "filters": body.filters, "max_results": body.max_results,
    }
    return _queued(db, project, user, payload)


@router.post("/{search_id}/rerun", response_model=SearchQueued, status_code=status.HTTP_202_ACCEPTED)
def queue_rerun(
    search_id: uuid.UUID,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    latest = db.scalar(
        select(SearchQuery)
        .where(SearchQuery.project_id == project.id, SearchQuery.search_id == search_id)
        .order_by(SearchQuery.version.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search not found")
    _usable(latest.database)
    if any((t.payload or {}).get("search_id") == str(search_id) for t in _open_tasks(db, project)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This search already has a run waiting or in progress")
    payload = {
        "project_id": str(project.id), "search_id": str(search_id), "version": latest.version + 1, "database": latest.database,
        "rerun": True, "max_results": DEFAULT_MAX_RESULTS,
    }
    return _queued(db, project, user, payload)


@router.get("")
def list_searches(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(SearchQuery).where(SearchQuery.project_id == project.id).order_by(SearchQuery.search_id, SearchQuery.version)
    ).all()
    searches: dict[uuid.UUID, dict[str, Any]] = {}
    for r in rows:
        entry = searches.setdefault(
            r.search_id, {"search_id": str(r.search_id), "database": r.database, "versions": []}
        )
        entry.update(query_string=r.query_string, filters=r.filters, exact=r.exact, caveats=r.caveats)
        entry["versions"].append({"version": r.version, "run_at": r.run_at, "n_results": r.n_results, "counts": r.counts})
    pending = [
        {
            "task_id": str(t.id), "search_id": (t.payload or {}).get("search_id"), "version": (t.payload or {}).get("version"),
            "database": (t.payload or {}).get("database"), "status": t.status.value,
            "blocked_by_gate": t.blocked_by_gate.value if t.blocked_by_gate else None,
        }
        for t in _open_tasks(db, project)
    ]
    return {"searches": list(searches.values()), "pending": pending}
