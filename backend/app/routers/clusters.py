"""Thematic clusters (M3.8.1): ask for a clustering, read the results, rename a cluster.

Clustering is off until an embedding model is configured (EMBEDDING_MODEL); while off, asking for one
is refused with 409 and nothing is queued. Labels are written only by people.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Artifact, Cluster, ClusterRun, Project, Source, User, utcnow
from app.task_handlers import THEMATIC_CLUSTERING
from app.task_queue import enqueue_task
from app.thematic_clusters import ARTIFACT_KIND, MAX_K, MIN_K, clusterable_sources

router = APIRouter(prefix="/projects/{project_id}/clusters", tags=["clusters"])


class ClusteringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k: int = Field(ge=MIN_K, le=MAX_K)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


class ClusteringQueued(BaseModel):
    task_id: uuid.UUID
    run_id: uuid.UUID
    status: str


class LabelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=200)

    @field_validator("label")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("A label can't be blank")
        return value


class ClusterMemberRead(BaseModel):
    source_id: uuid.UUID
    title: str
    distance: float


class ClusterRead(BaseModel):
    id: uuid.UUID
    position: int
    label: str
    label_edited_by: str | None
    label_edited_at: datetime | None
    members: list[ClusterMemberRead]


class ClusterRunRead(BaseModel):
    id: uuid.UUID
    model_id: str
    k: int
    seed: int
    n_sources: int
    created_by: str
    created_at: datetime
    status: str  # artifact status: "current", or "stale" after a re-entry to an earlier stage
    stale_reason: str | None
    clusters: list[ClusterRead]


def _read(db: Session, runs: list[ClusterRun]) -> list[ClusterRunRead]:
    source_ids = {m.source_id for run in runs for c in run.clusters for m in c.members}
    titles = dict(db.execute(select(Source.id, Source.title).where(Source.id.in_(source_ids))).all()) if source_ids else {}
    artifacts = {
        a.ref_id: a
        for a in db.scalars(select(Artifact).where(Artifact.kind == ARTIFACT_KIND, Artifact.ref_id.in_([str(r.id) for r in runs])))
    } if runs else {}
    out = []
    for run in runs:
        artifact = artifacts.get(str(run.id))
        out.append(ClusterRunRead(
            id=run.id, model_id=run.model_id, k=run.k, seed=run.seed, n_sources=run.n_sources, created_by=run.created_by,
            created_at=run.created_at, status=artifact.status.value if artifact else "current",
            stale_reason=artifact.stale_reason if artifact else None,
            clusters=[
                ClusterRead(
                    id=c.id, position=c.position, label=c.label, label_edited_by=c.label_edited_by, label_edited_at=c.label_edited_at,
                    members=[ClusterMemberRead(source_id=m.source_id, title=titles.get(m.source_id, ""), distance=m.distance) for m in c.members],
                )
                for c in run.clusters
            ],
        ))
    return out


def _runs_query(project: Project):
    return (
        select(ClusterRun)
        .where(ClusterRun.project_id == project.id)
        .options(selectinload(ClusterRun.clusters).selectinload(Cluster.members))
    )


@router.post("", response_model=ClusteringQueued, status_code=status.HTTP_202_ACCEPTED)
def request_clustering(
    payload: ClusteringRequest,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Group the project's sources into `k` themes. Runs in the background; the result appears in the list."""
    settings = get_settings()
    if not settings.embedding_model:
        raise HTTPException(status.HTTP_409_CONFLICT, "Thematic clustering is off: no embedding model is configured (EMBEDDING_MODEL).")
    n = len(clusterable_sources(db, project))
    if n < payload.k:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Clustering into {payload.k} groups needs at least {payload.k} sources; the project has {n}."
        )
    if n > settings.cluster_max_sources:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"The project has {n} sources; clustering is limited to {settings.cluster_max_sources}."
        )
    actor = audit.user_actor(user)
    run_id = uuid.uuid4()
    task = enqueue_task(
        db, project.id, THEMATIC_CLUSTERING,
        payload={"project_id": str(project.id), "run_id": str(run_id), "k": payload.k, "seed": payload.seed, "actor": actor},
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="clusters.requested", project_id=project.id,
        payload={"task_id": str(task.id), "run_id": str(run_id), "k": payload.k, "seed": payload.seed},
    )
    db.commit()
    return ClusteringQueued(task_id=task.id, run_id=run_id, status=task.status.value)


@router.get("", response_model=list[ClusterRunRead])
def list_clusterings(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    runs = list(db.scalars(_runs_query(project).order_by(ClusterRun.created_at.desc(), ClusterRun.id)))
    return _read(db, runs)


@router.get("/{clustering_id}", response_model=ClusterRunRead)
def get_clustering(clustering_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    run = db.scalar(_runs_query(project).where(ClusterRun.id == clustering_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clustering not found")
    return _read(db, [run])[0]


@router.patch("/{clustering_id}/clusters/{cluster_id}", response_model=ClusterRead)
def rename_cluster(
    clustering_id: uuid.UUID,
    cluster_id: uuid.UUID,
    payload: LabelUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """A person names a cluster. The old and new labels are not put in the audit payload (ids only)."""
    cluster = db.scalar(
        select(Cluster)
        .join(ClusterRun, ClusterRun.id == Cluster.run_id)
        .where(Cluster.id == cluster_id, Cluster.run_id == clustering_id, ClusterRun.project_id == project.id)
        .options(selectinload(Cluster.members))
    )
    if cluster is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")
    actor = audit.user_actor(user)
    cluster.label, cluster.label_edited_by, cluster.label_edited_at = payload.label, actor, utcnow()
    audit.record(db, actor=actor, action="clusters.label_changed", project_id=project.id,
                 payload={"run_id": str(clustering_id), "cluster_id": str(cluster.id)})
    db.commit()
    titles = dict(db.execute(select(Source.id, Source.title).where(Source.id.in_([m.source_id for m in cluster.members]))).all())
    return ClusterRead(
        id=cluster.id, position=cluster.position, label=cluster.label, label_edited_by=cluster.label_edited_by,
        label_edited_at=cluster.label_edited_at,
        members=[ClusterMemberRead(source_id=m.source_id, title=titles.get(m.source_id, ""), distance=m.distance) for m in cluster.members],
    )
