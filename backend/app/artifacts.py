"""Artifacts, their dependency edges, and re-entering an earlier stage (spec 13.5, Appendix C).

An artifact is a produced result tied to the stage that made it. Going back to an earlier stage
makes everything from later stages stale, plus anything that depends on a stale artifact through
an edge, and voids the human approvals (gates) that belong to the stages being redone. Nothing is
deleted: stale artifacts stay readable, flagged, with the reason. The audit log keeps who undid
what and what the previous decisions were.

Rules this keeps:
  * a gate approved for the old work cannot silently cover the new work (plan rule 21), so
    re-entry resets the downstream gates to pending; re-approval is a fresh human decision;
  * nothing is ever *auto*-approved or auto-refreshed: producers call `refresh_artifact` only
    when they actually regenerate a result.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.gates import GATE_STAGES, ensure_gates
from app.models import Artifact, ArtifactEdge, ArtifactStatus, GateCode, GateStatus, Project, ProjectStage, utcnow

STAGE_ORDER = list(ProjectStage)


class ArtifactError(ValueError):
    pass


class StageError(ArtifactError):
    """The requested stage change isn't allowed."""


def stage_index(stage: ProjectStage) -> int:
    return STAGE_ORDER.index(stage)


def gate_for_stage(stage: ProjectStage) -> GateCode | None:
    return next((code for code, gated in GATE_STAGES.items() if gated == stage), None)


# ---- registering ------------------------------------------------------------------------------

def _downstream_ids(db: Session, start_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Everything reachable from `start_ids` by following edges downstream (excluding the start set itself)."""
    seen: set[uuid.UUID] = set()
    queue = deque(start_ids)
    while queue:
        current = queue.popleft()
        for child in db.scalars(select(ArtifactEdge.downstream_id).where(ArtifactEdge.upstream_id == current)):
            if child not in seen and child not in start_ids:
                seen.add(child)
                queue.append(child)
    return seen


def add_edge(db: Session, upstream: Artifact, downstream: Artifact) -> None:
    """Record that `downstream` was built from `upstream`. Refuses self-edges, cross-project edges and cycles."""
    if upstream.id == downstream.id:
        raise ArtifactError("An artifact cannot depend on itself")
    if upstream.project_id != downstream.project_id:
        raise ArtifactError("Artifacts can only depend on artifacts in the same project")
    if db.scalar(
        select(ArtifactEdge.id).where(ArtifactEdge.upstream_id == upstream.id, ArtifactEdge.downstream_id == downstream.id)
    ):
        return
    if upstream.id in _downstream_ids(db, {downstream.id}):
        raise ArtifactError("That dependency would create a cycle")
    db.add(ArtifactEdge(project_id=upstream.project_id, upstream_id=upstream.id, downstream_id=downstream.id))
    db.flush()
    if upstream.status == ArtifactStatus.stale and downstream.status == ArtifactStatus.current:
        _mark_stale(downstream, f"Depends on stale artifact {upstream.kind}:{upstream.ref_id or '-'}")


def register_artifact(
    db: Session,
    project_id: uuid.UUID,
    kind: str,
    ref_id: str | uuid.UUID | None,
    stage: ProjectStage,
    *,
    actor: str,
    depends_on: tuple[Artifact, ...] = (),
) -> Artifact:
    """Record that a result exists. Idempotent per (project, kind, ref_id). The caller commits."""
    ref = str(ref_id) if ref_id is not None else ""
    artifact = db.scalar(select(Artifact).where(Artifact.project_id == project_id, Artifact.kind == kind, Artifact.ref_id == ref))
    if artifact is None:
        artifact = Artifact(project_id=project_id, kind=kind, ref_id=ref, stage=stage, created_by=actor)
        db.add(artifact)
        db.flush()
    for upstream in depends_on:
        add_edge(db, upstream, artifact)
    return artifact


def refresh_artifact(db: Session, artifact: Artifact) -> None:
    """A producer regenerated this result: it is current again. Does not touch what depends on it."""
    artifact.status = ArtifactStatus.current
    artifact.stale_reason = None
    artifact.stale_at = None


def _mark_stale(artifact: Artifact, reason: str) -> None:
    artifact.status = ArtifactStatus.stale
    artifact.stale_reason = reason[:2000]
    artifact.stale_at = utcnow()


# ---- re-entry ---------------------------------------------------------------------------------

@dataclass
class Reentry:
    from_stage: ProjectStage
    to_stage: ProjectStage
    reason: str
    stale: list[Artifact] = field(default_factory=list)  # newly made stale by this re-entry
    gates_reset: list[GateCode] = field(default_factory=list)
    stages_to_redo: list[tuple[ProjectStage, GateCode | None]] = field(default_factory=list)


def reenter_stage(db: Session, project: Project, target: ProjectStage, *, actor: str, reason: str) -> Reentry:
    """Send the project back to an earlier stage. The caller commits (everything happens in one transaction)."""
    current = project.stage
    if stage_index(target) >= stage_index(current):
        raise StageError(f"Re-entry must go back to an earlier stage; the project is already at '{current.value}'")
    cutoff = stage_index(target)
    result = Reentry(current, target, reason)
    note = f"Re-entered '{target.value}' from '{current.value}': {reason}"

    artifacts = db.scalars(select(Artifact).where(Artifact.project_id == project.id)).all()
    by_id = {a.id: a for a in artifacts}
    later = {a.id for a in artifacts if stage_index(a.stage) > cutoff}
    for artifact_id in later:
        artifact = by_id[artifact_id]
        if artifact.status == ArtifactStatus.current:
            _mark_stale(artifact, note)
            result.stale.append(artifact)
    # Something from an earlier stage that was built from a now-stale artifact is stale too.
    for artifact_id in _downstream_ids(db, later):
        artifact = by_id[artifact_id]
        if artifact.status == ArtifactStatus.current:
            _mark_stale(artifact, f"Depends on an artifact made stale by re-entering '{target.value}'")
            result.stale.append(artifact)

    # The approvals belonging to stages we are going back over are void: a person must decide again.
    for gate in ensure_gates(db, project):
        if stage_index(GATE_STAGES[gate.code]) > cutoff and gate.status != GateStatus.pending:
            audit.record(
                db, actor=actor, action="gate.reset", project_id=project.id,
                payload={"gate": gate.code.value, "previous_status": gate.status.value, "previous_decided_by": gate.decided_by,
                         "because": f"re-entered '{target.value}'"},
            )
            gate.status = GateStatus.pending
            gate.decided_by = None
            gate.decided_at = None
            gate.note = None
            result.gates_reset.append(gate.code)

    result.stages_to_redo = [(s, gate_for_stage(s)) for s in STAGE_ORDER if cutoff < stage_index(s) <= stage_index(current)]
    project.stage = target
    project.touch()
    audit.record(
        db, actor=actor, action="project.reentered", project_id=project.id,
        payload={"from": current.value, "to": target.value, "reason": reason, "stale": len(result.stale),
                 "gates_reset": [c.value for c in result.gates_reset]},
    )
    db.flush()
    return result


def _utc_seconds(value: datetime) -> datetime:
    """`value` in UTC, to the second. `created_at` comes from the database clock (whole seconds on SQLite,
    sometimes without a zone), `stale_at` from Python with microseconds; compare them on equal terms."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def unreplaced_stale(db: Session, project: Project, up_to: ProjectStage) -> list[Artifact]:
    """Stale results of `up_to` or an earlier stage that nothing has replaced yet (plan M0.5.9).

    A stale artifact counts as replaced once a *current* artifact of the same kind, produced at the same
    stage, was registered no earlier than it went stale (Decisions log 2026-10-07). Regenerating the same
    record (`refresh_artifact`) makes it current, so it is not listed at all.
    """
    limit = stage_index(up_to)
    artifacts = db.scalars(select(Artifact).where(Artifact.project_id == project.id)).all()
    relevant = [a for a in artifacts if stage_index(a.stage) <= limit]
    current = [a for a in relevant if a.status == ArtifactStatus.current]
    blocking = []
    for artifact in relevant:
        if artifact.status != ArtifactStatus.stale:
            continue
        went_stale = _utc_seconds(artifact.stale_at) if artifact.stale_at else None
        replaced = any(
            other.kind == artifact.kind
            and other.stage == artifact.stage
            and (went_stale is None or _utc_seconds(other.created_at) >= went_stale)
            for other in current
        )
        if not replaced:
            blocking.append(artifact)
    return sorted(blocking, key=lambda a: (stage_index(a.stage), a.kind, a.ref_id))


def rerun_path(db: Session, project: Project) -> list[dict]:
    """What has to be redone: stale artifacts grouped by the stage that produced them, in workflow order.

    Each step names the stage's gate (if it has one) and that gate's current status, because a person must
    approve it again once the stage's work is redone.
    """
    stale = db.scalars(
        select(Artifact).where(Artifact.project_id == project.id, Artifact.status == ArtifactStatus.stale)
    ).all()
    gates = {g.code: g for g in ensure_gates(db, project)}
    steps = []
    for stage in STAGE_ORDER:
        members = sorted((a for a in stale if a.stage == stage), key=lambda a: (a.kind, a.ref_id))
        if not members:
            continue
        code = gate_for_stage(stage)
        steps.append({"stage": stage, "gate": code, "gate_status": gates[code].status if code else None, "artifacts": members})
    return steps
