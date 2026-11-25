"""AI-suggested gaps and research questions, suggestions only (X.192).

Both routes read the deterministic X.190 candidates and the verified project sources, ask the project's model, and
store each valid proposal as a shared AI suggestion. Nothing is created: a person accepts the suggestion card and
then posts the gap or question through `POST …/gaps` / `POST …/research-questions`.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, suggestion_store
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import llm_for_project, require_ai_on
from app.ai_item_locks import locked_ids, locked_uuids
from app.config import get_settings
from app.coverage_matrix import papers_for_project
from app.database import get_db
from app.dependencies import WRITE_ROLES, current_user, project_access
from app.evidence_matrix import build as build_evidence_matrix
from app.extraction_schema import SchemaError
from app.gap_miner import candidates as gap_candidates
from app.gap_suggest import SourceView, SuggestionError, suggest_gaps, suggest_questions
from app.models import Project, Source, User

router = APIRouter(prefix="/projects/{project_id}", tags=["gap-suggestions"])

NOTHING_TO_SUGGEST = "There is nothing to suggest from yet: it needs gap candidates and at least two verified sources with extractions."


class RejectedRead(BaseModel):
    summary: str
    reason: str


class ProposalRead(BaseModel):
    suggestion_id: uuid.UUID
    type: str
    text: str
    source_ids: list[str]
    reason: str


class SuggestionsRead(BaseModel):
    proposals: list[ProposalRead]
    rejected: list[RejectedRead]
    model_id: str
    prompt_version: str
    note: str = "Suggestions only: nothing has been created. Accept a suggestion card, then add it yourself."


def _verified_sources(db: Session, project: Project) -> list[SourceView]:
    """Active, metadata-verified, non-retracted sources that have an extraction, in a stable order."""
    ids = [paper.source_id for paper in papers_for_project(db, project)]
    rows = db.scalars(
        select(Source).where(
            Source.project_id == project.id, Source.id.in_(ids), Source.id.not_in(locked_uuids(db, project.id, "source")),
            Source.metadata_verified.is_(True), Source.merged_into.is_(None),
        )
    ).all()
    rows = [row for row in rows if "retracted" not in (row.quality_flags or [])]
    rows.sort(key=lambda row: (row.title.casefold(), str(row.id)))
    return [SourceView(str(row.id), row.title, row.year, row.abstract) for row in rows]


def _inputs(db: Session, project: Project):
    try:
        matrix = build_evidence_matrix(db, project)
    except SchemaError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    locked = locked_ids(db, project.id, "source")
    papers = [paper for paper in papers_for_project(db, project) if str(paper.source_id) not in locked]
    candidates = gap_candidates(papers=papers, matrix=matrix)
    candidates = [c for c in candidates if not (set(map(str, c["paper_ids"])) & locked)]
    sources = _verified_sources(db, project)
    if not candidates or len(sources) < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_TO_SUGGEST)
    return candidates, sources


def _run(db: Session, project: Project, user: User, *, kind: str, purpose: str, produce) -> SuggestionsRead:
    require_ai_on(project)
    candidates, sources = _inputs(db, project)
    try:
        llm = llm_for_project(get_settings(), project, purpose=purpose)
        result = produce(llm, project.title, candidates, sources)
    except LLMConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except (LLMResponseError, SuggestionError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    proposals = []
    for item in result.items:
        text = item.statement if kind == "gap" else item.text
        target_id = hashlib.sha256(f"{kind}|{text}".encode("utf-8")).hexdigest()[:64]
        suggestion = suggestion_store.create_suggestion(
            db, project_id=project.id, target_kind=kind, target_id=target_id,
            content=f"[{item.type}] {text}", model_id=result.model_id, prompt_version=result.prompt_version,
            producing_agent=f"{kind}-suggestions", evidence=[{"source_id": sid} for sid in item.source_ids],
        )
        proposals.append(ProposalRead(suggestion_id=suggestion.id, type=item.type, text=text, source_ids=list(item.source_ids), reason=item.reason))
    audit.record(
        db, actor=audit.user_actor(user), action=f"{kind}.suggested", project_id=project.id,
        payload={"proposed": len(result.items), "rejected": len(result.rejected), "candidates": len(candidates), "sources": len(sources)},
        model_id=result.model_id, prompt_version=result.prompt_version,
    )
    db.commit()
    return SuggestionsRead(
        proposals=proposals, rejected=[RejectedRead(summary=r.summary, reason=r.reason) for r in result.rejected],
        model_id=result.model_id, prompt_version=result.prompt_version,
    )


@router.post("/gaps/suggest", response_model=SuggestionsRead)
def suggest_project_gaps(project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _run(db, project, user, kind="gap", purpose="gap_suggestions", produce=suggest_gaps)


@router.post("/research-questions/suggest", response_model=SuggestionsRead)
def suggest_project_questions(project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _run(db, project, user, kind="research_question", purpose="rq_suggestions", produce=suggest_questions)
