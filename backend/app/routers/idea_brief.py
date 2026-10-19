"""Idea brief for gate G1 (M4.7, spec 5.1): the chosen idea, its novelty comparators, and the feasibility checklist.

Ideas are captured as context items of kind `idea` (POST .../context). This router reads them, scores each one
against the project's sources with `app.idea_scoping`, and stores the scholar's own brief. Nothing here approves
G1: "ready" means the checklist is complete, and approval stays with a person in the gates list.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.agent.embeddings import OpenAICompatibleEmbeddings
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import require_ai_on
from app.config import Settings, get_settings
from app.connectors.access import is_enabled
from app.connectors.base import ConnectorError, SearchRequest
from app.connectors.factory import build_connector
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.idea_clustering import Idea, cluster_ideas
from app.idea_scoping import FEASIBILITY_ITEMS, Reference, feasibility_gaps, novelty
from app.landscape_scan import summarise
from app.llm_usage import ProjectMeter
from app.models import ContextKind, IdeaBrief, Project, ResearchContextItem, Source, User
from app.semantic_novelty import VectorError, semantic_novelty
from app.thematic_clusters import source_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/idea-brief", tags=["idea-brief"])

FeasibilityItem = Literal["data_availability", "method_fit", "ethics_risk", "time_and_skills"]
FeasibilityStatus = Literal["ok", "concern", "blocker", "unsure"]
NOTE = (
    "Capturing and choosing an idea is yours. Ready means the checklist is complete, not that G1 may pass: "
    "G1 is approved by a person in the gates list."
)


class FeasibilityAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: FeasibilityItem
    status: FeasibilityStatus
    note: str = Field(default="", max_length=2_000)


class IdeaBriefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str = Field(min_length=1, max_length=5_000)
    why_it_matters: str = Field(min_length=1, max_length=5_000)
    risks: str | None = Field(default=None, max_length=5_000)
    selected_idea_id: uuid.UUID | None = None
    feasibility: list[FeasibilityAnswerIn] = Field(default_factory=list, max_length=len(FEASIBILITY_ITEMS))

    @field_validator("problem", "why_it_matters")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("risks")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("feasibility")
    @classmethod
    def _each_item_once(cls, value: list[FeasibilityAnswerIn]) -> list[FeasibilityAnswerIn]:
        items = [answer.item for answer in value]
        if len(items) != len(set(items)):
            raise ValueError("each feasibility item may be answered once")
        return value


class ComparatorRead(BaseModel):
    source_id: str
    title: str
    year: int | None
    overlap: float
    shared_terms: list[str]


class NoveltyRead(BaseModel):
    idea_terms: list[str]
    distinctiveness: float | None
    comparators: list[ComparatorRead]


class IdeaRead(BaseModel):
    id: uuid.UUID
    content: str
    created_at: datetime
    novelty: NoveltyRead


class ClusterRead(BaseModel):
    member_ids: list[str]
    shared_terms: list[str]


class ConstructRead(BaseModel):
    term: str
    idea_ids: list[str]


class IdeaBriefRead(BaseModel):
    """`exists` is false until a brief is saved; `ideas` is always listed so the scholar can choose one."""

    exists: bool
    project_id: uuid.UUID
    problem: str | None = None
    why_it_matters: str | None = None
    risks: str | None = None
    selected_idea_id: uuid.UUID | None = None
    feasibility: list[dict] = Field(default_factory=list)
    open_items: list[str]
    blockers: list[str]
    ready_for_g1: bool
    ideas: list[IdeaRead]
    clusters: list[ClusterRead]
    constructs: list[ConstructRead]
    duplicate_ideas: dict[str, str]
    updated_by: str | None = None
    updated_at: datetime | None = None
    note: str = NOTE


def _brief(db: Session, project: Project) -> IdeaBrief | None:
    return db.scalar(select(IdeaBrief).where(IdeaBrief.project_id == project.id))


def _build(db: Session, project: Project, brief: IdeaBrief | None) -> IdeaBriefRead:
    ideas = db.scalars(
        select(ResearchContextItem)
        .where(ResearchContextItem.project_id == project.id, ResearchContextItem.kind == ContextKind.idea)
        .order_by(ResearchContextItem.created_at.asc(), ResearchContextItem.id.asc())
    ).all()
    # Merged-away sources are duplicates of a kept record; comparing against them would double-count.
    references: list[Reference] = []
    if ideas:
        sources = db.scalars(select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None))).all()
        references = [Reference(str(s.id), s.title, s.year, s.abstract or "") for s in sources]

    answers = {entry["item"]: entry["status"] for entry in (brief.feasibility if brief else [])}
    open_items = feasibility_gaps(answers)
    blockers = [item for item in FEASIBILITY_ITEMS if answers.get(item) == "blocker"]
    ready = bool(brief and brief.selected_idea_id and not open_items and not blockers)
    clustering = cluster_ideas([Idea(str(i.id), i.content) for i in ideas])
    return IdeaBriefRead(
        exists=brief is not None,
        project_id=project.id,
        problem=brief.problem if brief else None,
        why_it_matters=brief.why_it_matters if brief else None,
        risks=brief.risks if brief else None,
        selected_idea_id=brief.selected_idea_id if brief else None,
        feasibility=list(brief.feasibility) if brief else [],
        open_items=open_items,
        blockers=blockers,
        ready_for_g1=ready,
        ideas=[_idea_read(idea, references) for idea in ideas],
        clusters=[ClusterRead(member_ids=c.member_ids, shared_terms=c.shared_terms) for c in clustering.clusters],
        constructs=[ConstructRead(term=c.term, idea_ids=c.idea_ids) for c in clustering.constructs],
        duplicate_ideas=clustering.duplicates,
        updated_by=brief.updated_by if brief else None,
        updated_at=brief.updated_at if brief else None,
    )


def _idea_read(idea: ResearchContextItem, references: list[Reference]) -> IdeaRead:
    result = novelty(idea.content, references)
    return IdeaRead(
        id=idea.id,
        content=idea.content,
        created_at=idea.created_at,
        novelty=NoveltyRead(
            idea_terms=result.idea_terms,
            distinctiveness=result.distinctiveness,
            comparators=[ComparatorRead(**asdict(c)) for c in result.comparators],
        ),
    )


SEMANTIC_NOTE = (
    "Cosine similarity between embeddings of each idea and of each source's title and abstract. A comparison to read, "
    "not a verdict on novelty: the scholar decides. Nothing is stored; the embedding model is named in model_id."
)


class SemanticComparatorRead(BaseModel):
    source_id: str
    title: str
    year: int | None
    similarity: float


class SemanticIdeaRead(BaseModel):
    id: uuid.UUID
    content: str
    distinctiveness: float | None
    comparators: list[SemanticComparatorRead]


class SemanticNoveltyRead(BaseModel):
    model_id: str | None
    note: str = SEMANTIC_NOTE
    ideas: list[SemanticIdeaRead]


def get_embedder(
    settings: Settings = Depends(get_settings), project: Project = Depends(project_access(READ_ROLES))
) -> OpenAICompatibleEmbeddings:
    return OpenAICompatibleEmbeddings(
        settings,
        meter=ProjectMeter(project.id, settings, purpose="semantic_novelty", budget_override=project.token_budget_override),
    )


def _embed_all(embedder, texts: list[str], batch_size: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed(texts[start : start + batch_size]))
    if len(vectors) != len(texts):
        raise LLMResponseError("The embedding service returned the wrong number of vectors.")
    return vectors


@router.get("/semantic-novelty", response_model=SemanticNoveltyRead)
def get_semantic_novelty(
    project: Project = Depends(project_access(READ_ROLES)),
    embedder: OpenAICompatibleEmbeddings = Depends(get_embedder),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    require_ai_on(project, embeddings=True)
    ideas = db.scalars(
        select(ResearchContextItem)
        .where(ResearchContextItem.project_id == project.id, ResearchContextItem.kind == ContextKind.idea)
        .order_by(ResearchContextItem.created_at.asc(), ResearchContextItem.id.asc())
    ).all()
    # As in the lexical comparison: merged-away sources are duplicates of a kept record.
    sources = db.scalars(select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None))).all()
    if not ideas or not sources:
        # Nothing to compare: no embedding call, so no cost and no model output.
        return SemanticNoveltyRead(
            model_id=embedder.model,
            ideas=[SemanticIdeaRead(id=i.id, content=i.content, distinctiveness=None, comparators=[]) for i in ideas],
        )
    texts = [i.content for i in ideas] + [source_text(s) for s in sources]
    try:
        vectors = _embed_all(embedder, texts, settings.embedding_batch_size)
    except LLMConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    refs = [(Reference(str(s.id), s.title, s.year, ""), v) for s, v in zip(sources, vectors[len(ideas):])]
    try:
        scored = [semantic_novelty(v, refs) for v in vectors[: len(ideas)]]
    except VectorError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return SemanticNoveltyRead(
        model_id=embedder.model,
        ideas=[
            SemanticIdeaRead(
                id=idea.id,
                content=idea.content,
                distinctiveness=result.distinctiveness,
                comparators=[
                    SemanticComparatorRead(source_id=c.source_id, title=c.title, year=c.year, similarity=c.similarity)
                    for c in result.comparators
                ],
            )
            for idea, result in zip(ideas, scored)
        ],
    )


LANDSCAPE_CONNECTORS = ("openalex", "crossref", "semantic_scholar")  # first enabled one is used
LANDSCAPE_NOTE = (
    "What this one search returned, not the whole literature. The top-cited list is ranked within the returned sample, "
    "not globally; citation counts are as the service reports them; a blank count means the service gave none, not zero. "
    "Review titles are a heuristic: check each one. The scholar decides."
)
REVIEW_WINDOW_YEARS = 5


class ScanEntryRead(BaseModel):
    connector: str
    external_id: str
    title: str
    year: int | None
    cited_by_count: int | None
    doi: str | None
    url: str | None


class LandscapeScanRead(BaseModel):
    connector: str | None  # None when no query was given
    query: str
    total_reported: int | None
    reviews_since: int
    note: str = LANDSCAPE_NOTE
    sample_size: int
    undated: int
    uncounted: int
    duplicates_removed: int
    top_cited_in_sample: list[ScanEntryRead]
    recent_reviews: list[ScanEntryRead]
    volume_by_year_in_sample: dict[int, int]


@router.get("/landscape-scan", response_model=LandscapeScanRead)
def get_landscape_scan(
    query: str = Query(default="", max_length=300),
    limit: int = Query(default=25, ge=1, le=25),
    project: Project = Depends(project_access(READ_ROLES)),
):
    """One keyword search through the first enabled scholarly connector. Stateless: nothing is stored."""
    text = query.strip()
    reviews_since = datetime.now(timezone.utc).year - REVIEW_WINDOW_YEARS
    if not text:
        # Nothing to search: an empty scan, with no connector call.
        return LandscapeScanRead(
            connector=None, query="", total_reported=None, reviews_since=reviews_since, sample_size=0, undated=0,
            uncounted=0, duplicates_removed=0, top_cited_in_sample=[], recent_reviews=[], volume_by_year_in_sample={},
        )
    settings = get_settings()
    name = next(
        (n for n in LANDSCAPE_CONNECTORS if is_enabled(n, settings.connectors_enabled, settings.connectors_allow_scraping)),
        None,
    )
    if name is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No landscape connector is enabled on this server.")
    try:
        page = build_connector(name).search(SearchRequest(query=text, limit=limit))
    except ConnectorError as exc:
        logger.warning("landscape scan: %s search failed: %s", name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The literature service could not be reached or returned an error.") from exc
    scan = summarise(page.records, reviews_since=reviews_since)
    return LandscapeScanRead(
        connector=name,
        query=text,
        total_reported=page.total,
        reviews_since=reviews_since,
        sample_size=scan.sample_size,
        undated=scan.undated,
        uncounted=scan.uncounted,
        duplicates_removed=scan.duplicates_removed,
        top_cited_in_sample=[ScanEntryRead(**vars(e)) for e in scan.top_cited_in_sample],
        recent_reviews=[ScanEntryRead(**vars(e)) for e in scan.recent_reviews],
        volume_by_year_in_sample=scan.volume_by_year_in_sample,
    )


@router.get("", response_model=IdeaBriefRead)
def get_idea_brief(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return _build(db, project, _brief(db, project))


@router.put("", response_model=IdeaBriefRead)
def save_idea_brief(
    body: IdeaBriefIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if body.selected_idea_id is not None:
        idea = db.get(ResearchContextItem, body.selected_idea_id)
        if idea is None or idea.project_id != project.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Idea not found in this project.")
        if idea.kind != ContextKind.idea:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only a context item of kind idea can be selected.")
    brief = _brief(db, project)
    created = brief is None
    if brief is None:
        brief = IdeaBrief(project_id=project.id)
        db.add(brief)
    brief.problem = body.problem
    brief.why_it_matters = body.why_it_matters
    brief.risks = body.risks
    brief.selected_idea_id = body.selected_idea_id
    brief.feasibility = [answer.model_dump(mode="json") for answer in body.feasibility]
    brief.updated_by = str(user.id)
    answers = {answer.item: answer.status for answer in body.feasibility}
    audit.record(
        db, actor=audit.user_actor(user), action="idea_brief.created" if created else "idea_brief.updated", project_id=project.id,
        payload={
            "selected": body.selected_idea_id is not None,
            "open_items": len(feasibility_gaps(answers)),
            "blockers": sum(1 for s in answers.values() if s == "blocker"),
        },
    )
    db.commit()
    db.refresh(brief)
    return _build(db, project, brief)
