"""Thematic clustering of a project's sources (spec 5.5.1, plan M3.8.1).

Each source's title and abstract is embedded by the configured embedding model, and the vectors are
grouped with seeded k-means on cosine similarity. It is an aid for a person, not a decision:

* clusters start as "Cluster 1", "Cluster 2", ... and only a person renames them; no AI writes labels;
* nothing is screened, excluded or ranked by it;
* the same embeddings, k and seed give the same clusters, so a run can be reproduced;
* a run is an artifact of the synthesis stage, so going back to an earlier stage marks it stale.

Which sources: every source not merged into another, minus those a person excluded at either screening
stage. A source with no abstract is embedded from its title alone.

Embeddings are cached per (source, model, text hash). Any embedding failure stops the run; nothing is
clustered from a partial set.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.artifacts import register_artifact
from app.models import Cluster, ClusterMember, ClusterRun, Project, ProjectStage, Source, SourceEmbedding
from app.screening import STAGES, final_decision, rows_for_stage, screenable_sources
from app.untrusted_text import clean_untrusted

ARTIFACT_KIND = "thematic_clusters"
AGENT = "agent:clustering"  # producer of the artifact; `ClusterRun.created_by` is the person who asked
MAX_TITLE_CHARS = 300
MAX_ABSTRACT_CHARS = 4000
MAX_ITERATIONS = 100
MIN_K, MAX_K = 2, 30


class ClusteringError(ValueError):
    """The clustering can't be run as asked; the message says why. Nothing is stored."""


class Embedder(Protocol):
    model: str | None

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def clusterable_sources(db: Session, project: Project) -> list[Source]:
    """Sources not merged away and not excluded by a person at any screening stage, oldest first."""
    excluded: set[uuid.UUID] = set()
    for stage in STAGES:
        for source_id, rows in rows_for_stage(db, project, stage).items():
            final = final_decision(rows)
            if final is not None and final.decision == "exclude":
                excluded.add(source_id)
    return [s for s in screenable_sources(db, project) if s.id not in excluded]


def source_text(source: Source) -> str:
    title = clean_untrusted(source.title, MAX_TITLE_CHARS).text
    if not source.abstract:
        return title
    return f"{title}\n\n{clean_untrusted(source.abstract, MAX_ABSTRACT_CHARS).text}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_embeddings(
    db: Session, project: Project, sources: list[Source], embedder: Embedder, *, batch_size: int
) -> list[list[float]]:
    """One vector per source, in order. Reuses stored vectors; embeds (and stores) only what's missing."""
    model = embedder.model or ""
    texts = {s.id: source_text(s) for s in sources}
    hashes = {s.id: _hash(texts[s.id]) for s in sources}
    stored = {
        row.source_id: row.vector
        for row in db.scalars(
            select(SourceEmbedding).where(
                SourceEmbedding.project_id == project.id,
                SourceEmbedding.model == model,
                SourceEmbedding.source_id.in_([s.id for s in sources]),
            )
        )
        if row.text_hash == hashes[row.source_id]
    }
    missing = [s for s in sources if s.id not in stored]
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        vectors = embedder.embed([texts[s.id] for s in batch])
        if len(vectors) != len(batch):
            raise ClusteringError("The embedding service returned the wrong number of vectors.")
        for source, vector in zip(batch, vectors):
            db.add(SourceEmbedding(
                project_id=project.id, source_id=source.id, model=model, text_hash=hashes[source.id],
                dims=len(vector), vector=vector,
            ))
            stored[source.id] = vector
        db.flush()
    vectors = [stored[s.id] for s in sources]
    if len({len(v) for v in vectors}) > 1:
        raise ClusteringError("The stored embeddings have different lengths; they can't be compared.")
    return vectors


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise ClusteringError("An embedding is all zeros and can't be compared.")
    return [x / norm for x in vector]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _fill_empty(assignment: list[int], points: list[list[float]], centres: list[list[float]], k: int) -> list[int]:
    """Give each empty cluster the worst-fitting point of a cluster that can spare one (rare; keeps k groups)."""
    assignment = list(assignment)
    for c in range(k):
        if c not in assignment:
            spare = [i for i in range(len(points)) if assignment.count(assignment[i]) > 1]
            worst = min(spare, key=lambda i: (_dot(points[i], centres[assignment[i]]), i))
            assignment[worst] = c
    return assignment


def kmeans(vectors: list[list[float]], k: int, seed: int, max_iterations: int = MAX_ITERATIONS) -> tuple[list[int], list[float]]:
    """Spherical k-means with k-means++ seeding. Returns (cluster index per vector, cosine distance to its centre).

    Deterministic for a given input order, k and seed. A cluster that empties is re-seeded with the
    point farthest from its centre, so exactly k non-empty clusters come back.
    """
    if not MIN_K <= k <= len(vectors):
        raise ClusteringError(f"k must be between {MIN_K} and the number of sources ({len(vectors)}).")
    points = [_normalise(v) for v in vectors]
    rng = random.Random(seed)
    centres = [points[rng.randrange(len(points))]]
    while len(centres) < k:
        weights = [max(0.0, 1 - max(_dot(p, c) for c in centres)) for p in points]
        total = sum(weights)
        if total <= 0:
            raise ClusteringError(f"There are fewer than {k} distinct sources to group; choose a smaller k.")
        pick, running = rng.random() * total, 0.0
        for index, weight in enumerate(weights):
            running += weight
            if weight > 0 and running >= pick:
                centres.append(points[index])
                break
        else:
            centres.append(points[max(range(len(points)), key=weights.__getitem__)])

    assignment: list[int] = [-1] * len(points)
    for _ in range(max_iterations):
        new = _fill_empty([max(range(k), key=lambda c: (_dot(p, centres[c]), -c)) for p in points], points, centres, k)
        if new == assignment:
            break
        assignment = new
        for c in range(k):
            members = [points[i] for i, a in enumerate(assignment) if a == c]
            centres[c] = _normalise([sum(column) for column in zip(*members)])
    distances = [max(0.0, 1 - _dot(p, centres[a])) for p, a in zip(points, assignment)]
    return assignment, distances


def run_clustering(
    db: Session,
    project: Project,
    *,
    embedder: Embedder,
    k: int,
    seed: int,
    actor: str,
    batch_size: int,
    max_sources: int,
    run_id: uuid.UUID | None = None,
    ensure_owned: Callable[[Session], None] | None = None,
) -> ClusterRun:
    """Embed, group and store one clustering run. Raises `ClusteringError` or an `LLM*Error`. The caller commits."""
    if not embedder.model:
        raise ClusteringError("Thematic clustering is off: no embedding model is configured (EMBEDDING_MODEL).")
    sources = clusterable_sources(db, project)
    if len(sources) > max_sources:
        raise ClusteringError(f"The project has {len(sources)} sources; clustering is limited to {max_sources}.")
    if len(sources) < max(k, MIN_K):
        raise ClusteringError(f"Clustering into {k} groups needs at least {max(k, MIN_K)} sources; the project has {len(sources)}.")
    vectors = ensure_embeddings(db, project, sources, embedder, batch_size=batch_size)
    assignment, distances = kmeans(vectors, k, seed)
    if ensure_owned:
        ensure_owned(db)

    run = ClusterRun(id=run_id or uuid.uuid4(), project_id=project.id, model_id=embedder.model, k=k, seed=seed, n_sources=len(sources), created_by=actor)
    db.add(run)
    groups = sorted(range(k), key=lambda c: (-assignment.count(c), assignment.index(c)))
    for position, c in enumerate(groups, start=1):
        cluster = Cluster(position=position, label=f"Cluster {position}")
        cluster.members = sorted(
            (ClusterMember(source_id=s.id, distance=d) for s, a, d in zip(sources, assignment, distances) if a == c),
            key=lambda m: m.distance,
        )
        run.clusters.append(cluster)
    db.flush()
    register_artifact(db, project.id, ARTIFACT_KIND, run.id, ProjectStage.synthesized, actor=AGENT)
    audit.record(
        db, actor=actor, action="clusters.created", project_id=project.id,
        payload={"run_id": str(run.id), "k": k, "seed": seed, "n_sources": len(sources)}, model_id=embedder.model,
    )
    return run


__all__ = [
    "ARTIFACT_KIND", "ClusteringError", "MAX_K", "MIN_K", "clusterable_sources", "ensure_embeddings", "kmeans",
    "run_clustering", "source_text",
]
