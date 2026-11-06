"""DB-aware generation and human-review tracking for the findings-to-literature map (M6.9, G9, spec
5.10 and 8).

`app.interpretation` is the pure comparison. This module is the single place that reads `AnalysisRun`
stored output and human-verified `Extraction` fields (M3.5's evidence table) from the database, and
persists the result as one append-only batch of `InterpretationFinding` rows (enforced by
`app.ledger_guard`): regenerating the map after new data writes a new `batch_id` rather than editing
anything. `current_batch` reads only the newest batch for a project.

Confirming a finding is a separate append-only `InterpretationReview` row; `is_confirmed` reads only the
newest review. Nothing here lets an AI confirm a finding — `record_review` is only ever called by a
router that requires a signed-in user. `g9_readiness` is the one check G9's approval and EC-12.3 both
use: the map must exist and every finding in its current batch must carry a `confirmed` review from a
person, not merely the AI-computed classification.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import interpretation
from app.models import AnalysisRun, Extraction, InterpretationFinding, InterpretationReview, Project

GENERATOR = "interpretation-map"
CONFIRMED = "confirmed"
DISPUTED = "disputed"
REVIEW_DECISIONS = frozenset({CONFIRMED, DISPUTED})


class NoFindings(RuntimeError):
    """The project has no analysis run with a numeric result yet, so there is nothing to map."""


def _numeric_leaves(data: Any, prefix: str = "") -> Iterable[tuple[str, str, float]]:
    """Every (key, dotted path, value) leaf of a nested dict whose value is a real number (bools excluded)."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            yield str(key), path, float(value)
        elif isinstance(value, dict):
            yield from _numeric_leaves(value, path)


def project_literature_values(db: Session, project_id: uuid.UUID) -> list[interpretation.LiteratureValue]:
    """Every numeric field of every human-verified extraction (M3.5's evidence table), one value per field."""
    rows = db.scalars(
        select(Extraction).where(Extraction.project_id == project_id, Extraction.verified_by_human.is_(True))
    ).all()
    return [
        interpretation.LiteratureValue(source_id=str(row.source_id), extraction_id=str(row.id), field_key=key, value=value)
        for row in rows
        for key, _path, value in _numeric_leaves(row.fields_json or {})
    ]


def run_findings(run: AnalysisRun) -> list[interpretation.AnalysisFinding]:
    """Every numeric leaf of one analysis run's stored output, as a comparable finding."""
    return [
        interpretation.AnalysisFinding(run_id=str(run.id), output_key=key, path=path, value=value)
        for key, path, value in _numeric_leaves(run.output_json or {})
    ]


def _next_generation(db: Session, project_id: uuid.UUID) -> int:
    current = db.scalar(select(func.max(InterpretationFinding.generation)).where(InterpretationFinding.project_id == project_id))
    return 1 if current is None else current + 1


def generate_map(db: Session, project: Project, *, actor: str) -> list[InterpretationFinding]:
    """Build a fresh findings-to-literature map over every stored analysis run and persist it as one new
    append-only batch, one `generation` higher than any before it. The caller commits. Raises
    `NoFindings` if no run has a numeric output yet."""
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project.id).order_by(AnalysisRun.created_at)).all()
    literature = project_literature_values(db, project.id)
    batch_id = uuid.uuid4()
    generation = _next_generation(db, project.id)
    rows: list[InterpretationFinding] = []
    for run in runs:
        for finding in run_findings(run):
            item = interpretation.classify(finding, literature)
            row = InterpretationFinding(
                project_id=project.id,
                batch_id=batch_id,
                generation=generation,
                analysis_run_id=run.id,
                output_key=item.finding.output_key,
                output_path=item.finding.path,
                value=item.finding.value,
                classification=item.classification.value,
                literature_source_ids=list(item.literature_source_ids),
                literature_extraction_ids=list(item.literature_extraction_ids),
                rationale=item.rationale,
                generated_by=actor,
            )
            db.add(row)
            rows.append(row)
    if not rows:
        raise NoFindings("No analysis run has a numeric result yet, so there is nothing to map to the literature.")
    return rows


def current_batch(db: Session, project_id: uuid.UUID) -> list[InterpretationFinding]:
    """Every finding in the highest-`generation` batch, oldest first. Empty if none was ever generated.
    `generation`, not `created_at`, decides "latest": two batches generated inside the same second would
    otherwise tie on a clock with only second resolution (SQLite's `CURRENT_TIMESTAMP`)."""
    latest = db.scalar(select(func.max(InterpretationFinding.generation)).where(InterpretationFinding.project_id == project_id))
    if latest is None:
        return []
    return list(
        db.scalars(
            select(InterpretationFinding)
            .where(InterpretationFinding.project_id == project_id, InterpretationFinding.generation == latest)
            .order_by(InterpretationFinding.created_at)
        )
    )


def finding_in_project(db: Session, project_id: uuid.UUID, finding_id: uuid.UUID) -> InterpretationFinding | None:
    return db.scalar(
        select(InterpretationFinding).where(InterpretationFinding.id == finding_id, InterpretationFinding.project_id == project_id)
    )


def latest_review(db: Session, finding_id: uuid.UUID) -> InterpretationReview | None:
    """The highest-`seq` review of `finding_id`. `seq`, not `created_at`, decides "latest" for the same
    clock-resolution reason as `current_batch`'s `generation`."""
    return db.scalar(
        select(InterpretationReview)
        .where(InterpretationReview.finding_id == finding_id)
        .order_by(InterpretationReview.seq.desc())
        .limit(1)
    )


def is_confirmed(db: Session, finding_id: uuid.UUID) -> bool:
    review = latest_review(db, finding_id)
    return review is not None and review.decision == CONFIRMED


def record_review(
    db: Session, project_id: uuid.UUID, finding: InterpretationFinding, *, decision: str, reviewed_by: str, note: str | None = None,
) -> InterpretationReview:
    """Append one person's confirm/dispute of `finding`, one `seq` higher than any review of it before. The
    caller commits."""
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"unknown review decision: {decision!r}")
    current_seq = db.scalar(select(func.max(InterpretationReview.seq)).where(InterpretationReview.finding_id == finding.id))
    row = InterpretationReview(
        project_id=project_id, finding_id=finding.id, seq=1 if current_seq is None else current_seq + 1,
        decision=decision, note=note, reviewed_by=reviewed_by,
    )
    db.add(row)
    return row


def to_item(row: InterpretationFinding) -> interpretation.InterpretationItem:
    """Rebuild the pure `InterpretationItem` a stored finding row was computed from, for drafting."""
    finding = interpretation.AnalysisFinding(run_id=str(row.analysis_run_id), output_key=row.output_key, path=row.output_path, value=row.value)
    return interpretation.InterpretationItem(
        finding=finding,
        classification=interpretation.Agreement(row.classification),
        literature_source_ids=tuple(row.literature_source_ids or []),
        literature_extraction_ids=tuple(row.literature_extraction_ids or []),
        rationale=row.rationale,
    )


@dataclass(frozen=True)
class G9Readiness:
    ready: bool
    reason: str


def g9_readiness(db: Session, project_id: uuid.UUID) -> G9Readiness:
    """Whether G9 (EC-12.3) can be approved: the current findings-to-literature map batch exists and every
    finding in it carries a `confirmed` review from a person. An AI-computed classification alone is
    never enough (research-integrity rule: AI never silently decides substantive correctness)."""
    batch = current_batch(db, project_id)
    if not batch:
        return G9Readiness(False, "no findings-to-literature map has been generated yet")
    unconfirmed = sum(1 for row in batch if not is_confirmed(db, row.id))
    if unconfirmed:
        return G9Readiness(False, f"{unconfirmed} of {len(batch)} finding(s) in the map have not been confirmed by a person")
    return G9Readiness(True, "")
