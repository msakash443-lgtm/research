"""Validation and deterministic ranking rules for M3.9 literature gaps."""

from __future__ import annotations

from collections.abc import Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coverage_matrix import DIMENSIONS, build_matrix, papers_for_project
from app.models import Project, Source

GAP_TYPES = frozenset({"theoretical", "methodological", "empirical", "contextual", "population", "temporal"})


def validate_gap_evidence(paper_ids: Sequence[str], cell_ids: Sequence[str]) -> None:
    if len(set(paper_ids)) < 2 and not cell_ids:
        raise ValueError("a gap needs at least two supporting papers or matrix evidence")


def validate_gap_type(gap_type: str) -> None:
    if gap_type not in GAP_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(GAP_TYPES))}")


def validate_project_evidence(db: Session, project: Project, paper_ids: Sequence[uuid.UUID], cell_ids: Sequence[str]) -> None:
    """Reject evidence that is not an active source or a real project matrix cell."""
    if paper_ids:
        found = set(db.scalars(select(Source.id).where(
            Source.project_id == project.id, Source.id.in_(paper_ids), Source.merged_into.is_(None)
        )))
        if found != set(paper_ids):
            raise ValueError("supporting papers must be active sources in this project")
    if cell_ids:
        papers = papers_for_project(db, project)
        valid: set[str] = set()
        dimensions = list(DIMENSIONS)
        for rows, cols in ((dimensions[0], dimensions[1]), (dimensions[0], dimensions[2]), (dimensions[1], dimensions[2])):
            for layer in (None, ({d for d in dimensions} - {rows, cols}).pop()):
                matrix = build_matrix(papers, rows, cols, layer)
                for item in matrix["matrices"]:
                    valid.update(c["cell_id"] for c in item["cells"])
                    valid.update(c["cell_id"] for c in item["empty_cells"])
        if set(cell_ids) - valid:
            raise ValueError("matrix evidence must identify a real cell in this project's coverage matrix")
