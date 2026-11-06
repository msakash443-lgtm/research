"""Shared logic for adding the researcher's own paper straight into the retained set (plan M2.14).

Used by both the immediate path (`POST .../sources/own`, fields or BibTeX given directly,
`routers/sources.py::add_own_paper`) and the queued DOI/title lookup path (M2.14.1,
`app/own_paper_lookup.py` + `handle_own_paper_lookup`): either way the result is an unverified
`origin=manual` source with the caller's own `include` decision recorded at every screening stage,
exactly like any other human screening decision (undoable, auditable; under dual-screen it still
waits for a second reviewer).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import audit
from app.artifacts import register_artifact
from app.models import Project, ProjectStage, ScreeningDecision, Source
from app.schemas import SourceCreate
from app.screening import STAGES

SCREENING_KIND = "screening_decision"  # same artifact kind as routers/screening.py
LOCKED_MESSAGE = "Gate G3 is approved for this screening. Reopen the project at the screening stage to add a paper."


def create_own_paper_source(db: Session, *, project: Project, actor: str, item: SourceCreate, note: str = "added by researcher") -> Source:
    source = Source(project_id=project.id, created_by=actor, **item.model_dump(mode="json", exclude={"evidence_excerpt", "locator"}))
    db.add(source)
    db.flush()
    for stage in STAGES:
        row = ScreeningDecision(
            project_id=project.id, source_id=source.id, stage=stage, seq=1, decision="include",
            decided_by="human", decider=actor, note=note,
        )
        db.add(row)
        db.flush()
        register_artifact(db, project.id, SCREENING_KIND, str(row.id), ProjectStage.screened, actor=actor)
    audit.record(
        db, actor=actor, action="source.own_paper_added", project_id=project.id,
        payload={"source_id": str(source.id), "has_doi": bool(item.doi)},
    )
    project.touch()
    return source
