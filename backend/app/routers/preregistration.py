"""Pre-registration draft (M4.6). Read-only and stateless: the draft is generated on request, nothing is stored.

Hypotheses are approved through gate G6; this endpoint does not change any gate and submits nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.models import Hypothesis, Project, ResearchQuestion
from app.preregistration import RQ, HypothesisItem, build_preregistration

router = APIRouter(prefix="/projects/{project_id}/preregistration", tags=["preregistration"])


@router.get("")
def get_preregistration(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    questions = db.scalars(
        select(ResearchQuestion).where(ResearchQuestion.project_id == project.id).order_by(ResearchQuestion.created_at.asc())
    ).all()
    by_id = {q.id: q for q in questions}
    hypotheses = db.scalars(
        select(Hypothesis).where(Hypothesis.project_id == project.id).order_by(Hypothesis.created_at.asc())
    ).all()
    items = [
        HypothesisItem(h.statement, h.direction, by_id[h.research_question_id].text)
        for h in hypotheses
        if h.research_question_id in by_id
    ]
    draft = build_preregistration(project.title, [RQ(q.text, q.type) for q in questions], items)
    return {
        "gate": "G6",
        "note": draft.note,
        "forms": ["aspredicted", "osf"],
        "unhypothesised_questions": draft.unhypothesised_questions,
        "hypothesis_count": draft.hypothesis_count,
        "aspredicted": draft.aspredicted,
        "osf": {"sections": draft.osf_sections},
    }
