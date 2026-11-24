"""Research-plan document (X.194): one read-only view assembled from what people already saved. No model, no writes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import research_plan
from app.database import get_db
from app.dependencies import READ_ROLES, project_access
from app.gates import GATE_STAGES
from app.models import (
    AnalysisPlan, Gap, Gate, Hypothesis, IdeaBrief, LandscapeScanRun, Project, ResearchContextItem,
    ResearchObjective, ResearchQuestion,
)
from app.stage_order import chain

router = APIRouter(prefix="/projects/{project_id}/research-plan", tags=["research plan"])


def _idea_brief(db: Session, project: Project) -> dict[str, Any] | None:
    brief = db.scalar(select(IdeaBrief).where(IdeaBrief.project_id == project.id))
    if brief is None:
        return None
    idea = db.get(ResearchContextItem, brief.selected_idea_id) if brief.selected_idea_id else None
    return {
        "problem": brief.problem, "why_it_matters": brief.why_it_matters, "risks": brief.risks,
        "selected_idea_id": str(brief.selected_idea_id) if brief.selected_idea_id else None,
        "selected_idea": idea.content if idea is not None and idea.project_id == project.id else None,
        "updated_by": brief.updated_by,
    }


def _landscape_scan(db: Session, project: Project) -> dict[str, Any] | None:
    run = db.scalar(select(LandscapeScanRun).where(LandscapeScanRun.project_id == project.id).order_by(LandscapeScanRun.run_at.desc()).limit(1))
    if run is None:
        return None
    return {
        "id": str(run.id), "query": run.query, "run_at": run.run_at.isoformat(), "record_count": len(run.record_ids),
        "services": [{"service": s["service"], "status": s["status"], "count": s.get("count")} for s in run.services],
    }


def _milestones(db: Session, project: Project) -> list[dict[str, Any]]:
    status_of = {g.code: g.status.value for g in db.scalars(select(Gate).where(Gate.project_id == project.id))}
    gate_of = {stage: code for code, stage in GATE_STAGES.items()}
    items = []
    for stage in chain(project):
        code = gate_of.get(stage)
        items.append({
            "stage": stage.value, "current": stage == project.stage,
            "gate": code.value if code else None, "gate_status": status_of.get(code, "pending") if code else None,
        })
    return items


def assemble(db: Session, project: Project) -> dict[str, Any]:
    gaps = db.scalars(select(Gap).where(Gap.project_id == project.id, Gap.selected.is_(True)).order_by(Gap.priority.desc(), Gap.created_at, Gap.id)).all()
    questions = db.scalars(select(ResearchQuestion).where(ResearchQuestion.project_id == project.id).order_by(ResearchQuestion.created_at, ResearchQuestion.id)).all()
    objectives = db.scalars(
        select(ResearchObjective).where(ResearchObjective.project_id == project.id, ResearchObjective.retired_at.is_(None)).order_by(ResearchObjective.created_at, ResearchObjective.id)
    ).all()
    hypotheses = db.scalars(select(Hypothesis).where(Hypothesis.project_id == project.id).order_by(Hypothesis.created_at, Hypothesis.id)).all()
    plans = db.scalars(
        select(AnalysisPlan).where(AnalysisPlan.project_id == project.id, AnalysisPlan.locked_at.is_not(None)).order_by(AnalysisPlan.locked_at, AnalysisPlan.id)
    ).all()
    return research_plan.build(
        idea_brief=_idea_brief(db, project),
        landscape_scan=_landscape_scan(db, project),
        gaps=[
            {"id": str(g.id), "type": g.type, "statement": g.statement, "supporting_paper_ids": [str(i) for i in g.supporting_paper_ids], "priority": g.priority}
            for g in gaps
        ],
        questions=[{"id": str(q.id), "text": q.text, "type": q.type, "gap_ids": [str(link.gap_id) for link in q.gap_links]} for q in questions],
        objectives=[
            {
                "id": str(o.id), "research_question_id": o.research_question_id, "statement": o.statement, "measure": o.measure,
                "deliverable": o.deliverable, "target_date": o.target_date.isoformat() if o.target_date else None,
            }
            for o in objectives
        ],
        hypotheses=[
            {"id": str(h.id), "research_question_id": h.research_question_id, "statement": h.statement, "direction": h.direction}
            for h in hypotheses
        ],
        analysis_plans=[
            {"id": str(p.id), "dataset_id": str(p.dataset_id), "plan_sha256": p.plan_sha256, "locked_at": p.locked_at.isoformat(), "locked_by": p.locked_by}
            for p in plans
        ],
        milestones=_milestones(db, project),
    )


@router.get("", response_model=None)
def get_research_plan(
    format: Literal["json", "md"] = Query("json"),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    plan = assemble(db, project)
    if format == "md":
        return Response(content=research_plan.to_markdown(plan, project.title), media_type="text/markdown; charset=utf-8")
    return plan
