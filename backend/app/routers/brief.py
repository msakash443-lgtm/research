"""Researcher brief: topic, keyword groups, scope and minimum-citation threshold (M2.12, spec 5.2.1).

The brief is the researcher's own words. `GET .../brief/query` turns its keyword groups into the concept
blocks and Boolean string the search builder takes; it queues and runs nothing (searches stay behind gate G2).
The citation threshold is only stored here; filtering by it needs the citation-count field of M2.13.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, ResearchBrief, User
from app.search_query import MAX_BLOCKS, BooleanQuery, ConceptBlock, QueryError, render

router = APIRouter(prefix="/projects/{project_id}/brief", tags=["brief"])


class BriefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=2_000)
    keyword_groups: list[ConceptBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)
    scope: str | None = Field(default=None, max_length=20_000)
    min_citations: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("topic")
    @classmethod
    def _topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class BriefRead(BaseModel):
    """`exists` is false until a brief is saved; the other fields are then empty (a read never fails at any stage)."""

    exists: bool
    project_id: uuid.UUID
    topic: str | None = None
    keyword_groups: list[dict[str, Any]] = Field(default_factory=list)
    scope: str | None = None
    min_citations: int | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


class BriefQuery(BaseModel):
    blocks: list[dict[str, Any]]
    query_string: str  # empty when the brief has no keyword groups yet
    min_citations: int | None
    note: str = "A draft built from your brief. Nothing has been searched; review it, then queue the search."


def _read(item: ResearchBrief) -> BriefRead:
    return BriefRead(
        exists=True, project_id=item.project_id, topic=item.topic, keyword_groups=item.keyword_groups, scope=item.scope,
        min_citations=item.min_citations, updated_by=item.updated_by, updated_at=item.updated_at,
    )


@router.get("", response_model=BriefRead)
def get_brief(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    item = db.scalar(select(ResearchBrief).where(ResearchBrief.project_id == project.id))
    return _read(item) if item else BriefRead(exists=False, project_id=project.id)


@router.put("", response_model=BriefRead)
def save_brief(
    body: BriefIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if body.keyword_groups:
        try:
            BooleanQuery(blocks=tuple(body.keyword_groups))  # refuse groups the builder cannot use, now
        except QueryError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    groups = [g.model_dump(mode="json") for g in body.keyword_groups]
    item = db.scalar(select(ResearchBrief).where(ResearchBrief.project_id == project.id))
    created = item is None
    if item is None:
        item = ResearchBrief(project_id=project.id)
        db.add(item)
    item.topic, item.keyword_groups, item.scope = body.topic, groups, body.scope
    item.min_citations, item.updated_by = body.min_citations, str(user.id)
    audit.record(
        db, actor=audit.user_actor(user), action="brief.created" if created else "brief.updated", project_id=project.id,
        payload={"keyword_groups": len(groups), "min_citations": body.min_citations},
    )
    db.commit()
    db.refresh(item)
    return _read(item)


@router.get("/query", response_model=BriefQuery)
def brief_query(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    """The brief's keyword groups as concept blocks and the generic Boolean string. Runs no search."""
    item = db.scalar(select(ResearchBrief).where(ResearchBrief.project_id == project.id))
    if item is None or not item.keyword_groups:
        return BriefQuery(blocks=[], query_string="", min_citations=item.min_citations if item else None)
    try:
        query = BooleanQuery(blocks=tuple(ConceptBlock.model_validate(g) for g in item.keyword_groups))
        text = render(query)
    except (QueryError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return BriefQuery(blocks=item.keyword_groups, query_string=text, min_citations=item.min_citations)
