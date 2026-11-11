"""Human-planned, G8-locked pooling of verified correlation extractions."""

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.extraction_verification import current_rows, schema_for_row
from app.extraction_schema import SchemaError
from app.gates import gate_is_approved
from app.meta_analysis import MetaAnalysisError, pool
from app.models import GateCode, MetaAnalysisPlan, Project, User

router = APIRouter(prefix="/projects/{project_id}/meta-analysis", tags=["meta-analysis"])
CORRELATION = re.compile(r"\s*r\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*", re.I)


class StudyChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extraction_id: uuid.UUID
    finding_index: int = Field(ge=0)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(min_length=1, max_length=200)
    primary_model: Literal["fixed", "random"]
    studies: list[StudyChoice] = Field(min_length=2, max_length=100)


def _studies(db: Session, project: Project, choices: list[dict]) -> list[dict]:
    current = {row.id: (source, row) for source, row in current_rows(db, project) if row is not None}
    studies = []
    for choice in choices:
        pair = current.get(uuid.UUID(choice["extraction_id"]))
        if pair is None or not pair[1].verified_by_human:
            raise HTTPException(409, "Every selected extraction must be current and human verified")
        source, row = pair
        try:
            schema_for_row(db, project, row)
        except SchemaError as exc:
            raise HTTPException(409, "Selected extraction uses an outdated schema") from exc
        findings = row.fields_json.get("key_findings")
        index = choice["finding_index"]
        finding = findings[index] if isinstance(findings, list) and index < len(findings) else None
        match = CORRELATION.fullmatch(finding.get("effect_size", "") or "") if isinstance(finding, dict) else None
        sample = row.fields_json.get("sample")
        n = sample.get("n") if isinstance(sample, dict) else None
        if match is None or not isinstance(n, int) or isinstance(n, bool):
            raise HTTPException(422, "Selected finding needs an explicit r value and integer sample n")
        studies.append({"extraction_id": str(row.id), "source_id": str(source.id), "r": float(match.group(1)), "n": n})
    try:
        pool(studies)
    except MetaAnalysisError as exc:
        raise HTTPException(422, str(exc)) from exc
    return studies


@router.post("", status_code=201)
def create_plan(payload: PlanIn, project: Project = Depends(project_access(WRITE_ROLES)),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.refresh(project, with_for_update=True)
    if gate_is_approved(db, project.id, GateCode.G8):
        raise HTTPException(409, "G8 has locked the analysis plan")
    choices = [choice.model_dump(mode="json") for choice in payload.studies]
    _studies(db, project, choices)
    row = MetaAnalysisPlan(project_id=project.id, outcome=payload.outcome.strip(), primary_model=payload.primary_model,
                           studies_json=choices, created_by=audit.user_actor(user))
    db.add(row)
    db.flush()
    audit.record(db, actor=row.created_by, action="meta_analysis.planned", project_id=project.id,
                 payload={"plan_id": str(row.id), "study_count": len(choices)})
    db.commit()
    return {"id": str(row.id), "outcome": row.outcome, "primary_model": row.primary_model, "studies": row.studies_json}


@router.get("")
def list_plans(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    rows = db.scalars(select(MetaAnalysisPlan).where(MetaAnalysisPlan.project_id == project.id).order_by(MetaAnalysisPlan.created_at)).all()
    return [{"id": str(row.id), "outcome": row.outcome, "primary_model": row.primary_model, "studies": row.studies_json,
             "result": row.result_json} for row in rows]


@router.post("/{plan_id}/run")
def run_plan(plan_id: uuid.UUID, project: Project = Depends(project_access(WRITE_ROLES)),
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.refresh(project, with_for_update=True)
    row = db.scalar(select(MetaAnalysisPlan).where(MetaAnalysisPlan.id == plan_id,
                                                    MetaAnalysisPlan.project_id == project.id).with_for_update())
    if row is None:
        raise HTTPException(404, "Analysis plan not found")
    if not gate_is_approved(db, project.id, GateCode.G8):
        raise HTTPException(409, "G8 must be approved by a person before analysis")
    studies = _studies(db, project, row.studies_json)
    if row.result_json is not None:
        previous = [{key: point[key] for key in ("extraction_id", "source_id", "r", "n")}
                    for point in row.result_json["forest"]]
        if previous != studies:
            raise HTTPException(409, "Verified effect sizes changed; create a new analysis plan before G8")
        return row.result_json
    result = {"plan_id": str(row.id), "outcome": row.outcome, "primary_model": row.primary_model, **pool(studies)}
    row.result_json = result
    audit.record(db, actor=audit.user_actor(user), action="meta_analysis.computed", project_id=project.id,
                 payload={"plan_id": str(row.id), "study_count": len(studies)})
    db.commit()
    return result
