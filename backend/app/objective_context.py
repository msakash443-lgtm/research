"""Live research objectives (X.193) shaped as context items, for the paths that read context items.

`ResearchObjective` is the only active objective record, but a research run's prompt and the Obsidian export
are built from context items. This turns each live objective into a transient (never added to a session)
`ResearchContextItem` of kind `objective`, so those paths carry objectives without a second code path. The
item's id is the objective's id; its rationale also carries the measure, deliverable and target date.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContextKind, ResearchContextItem, ResearchObjective


def _detail(objective: ResearchObjective) -> str | None:
    parts = [
        objective.rationale,
        f"Success is judged by: {objective.measure}" if objective.measure else None,
        f"Deliverable: {objective.deliverable}" if objective.deliverable else None,
        f"Target date: {objective.target_date.isoformat()}" if objective.target_date else None,
    ]
    text = "\n".join(part for part in parts if part)
    return text or None


def live_objective_items(db: Session, project_id: uuid.UUID) -> list[ResearchContextItem]:
    objectives = db.scalars(
        select(ResearchObjective)
        .where(ResearchObjective.project_id == project_id, ResearchObjective.retired_at.is_(None))
        .order_by(ResearchObjective.created_at, ResearchObjective.id)
    ).all()
    return [
        ResearchContextItem(
            id=objective.id, project_id=project_id, kind=ContextKind.objective, content=objective.statement,
            rationale=_detail(objective), created_by=objective.created_by, created_at=objective.created_at,
            retired_at=None,
        )
        for objective in objectives
    ]
