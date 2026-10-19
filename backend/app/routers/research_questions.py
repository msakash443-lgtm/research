"""Human-owned research-question and hypothesis drafting (M4.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Gap, Hypothesis, Project, ResearchQuestion, ResearchQuestionGap, User

router = APIRouter(prefix="/projects/{project_id}", tags=["research questions"])


class ResearchQuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)
    type: str = Field(min_length=1, max_length=30)
    gap_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @field_validator("text", "type")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ResearchQuestionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=20_000)
    type: str | None = Field(default=None, min_length=1, max_length=30)
    gap_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)

    @field_validator("text", "type")
    @classmethod
    def optional_text_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


class ResearchQuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    text: str
    type: str
    gap_ids: list[uuid.UUID]
    created_by: str
    created_at: datetime
    updated_at: datetime


class HypothesisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_question_id: uuid.UUID
    statement: str = Field(min_length=1, max_length=20_000)
    direction: str = Field(min_length=1, max_length=30)
    theoretical_basis: str = Field(min_length=1, max_length=20_000)
    empirical_support: str = Field(min_length=1, max_length=20_000)

    @field_validator("statement", "direction", "theoretical_basis", "empirical_support")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class HypothesisPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_question_id: uuid.UUID | None = None
    statement: str | None = Field(default=None, min_length=1, max_length=20_000)
    direction: str | None = Field(default=None, min_length=1, max_length=30)
    theoretical_basis: str | None = Field(default=None, min_length=1, max_length=20_000)
    empirical_support: str | None = Field(default=None, min_length=1, max_length=20_000)

    @field_validator("statement", "direction", "theoretical_basis", "empirical_support")
    @classmethod
    def optional_text_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value


class HypothesisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    research_question_id: uuid.UUID
    statement: str
    direction: str
    theoretical_basis: str
    empirical_support: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class FinerItem(BaseModel):
    criterion: str
    status: str
    guidance: str


class FinerCritique(BaseModel):
    research_question_id: uuid.UUID
    linked_gap_count: int
    items: list[FinerItem]
    disclaimer: str


def _question(db: Session, project: Project, item_id: uuid.UUID) -> ResearchQuestion:
    item = db.scalar(
        select(ResearchQuestion)
        .options(selectinload(ResearchQuestion.gap_links))
        .where(ResearchQuestion.id == item_id, ResearchQuestion.project_id == project.id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Research question not found")
    return item


def _hypothesis(db: Session, project: Project, item_id: uuid.UUID) -> Hypothesis:
    item = db.scalar(select(Hypothesis).where(Hypothesis.id == item_id, Hypothesis.project_id == project.id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hypothesis not found")
    return item


def _gap_ids(item: ResearchQuestion) -> list[uuid.UUID]:
    return [link.gap_id for link in item.gap_links]


def _question_read(item: ResearchQuestion) -> ResearchQuestionRead:
    return ResearchQuestionRead(
        id=item.id, project_id=item.project_id, text=item.text, type=item.type,
        gap_ids=_gap_ids(item), created_by=item.created_by, created_at=item.created_at, updated_at=item.updated_at,
    )


def _require_selected_gaps(db: Session, project: Project, gap_ids: list[uuid.UUID]) -> None:
    if len(set(gap_ids)) != len(gap_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A gap may be linked only once")
    selected = set(db.scalars(select(Gap.id).where(Gap.project_id == project.id, Gap.selected.is_(True), Gap.id.in_(gap_ids))).all())
    if selected != set(gap_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Every linked gap must be selected in this project")


@router.get("/research-questions", response_model=list[ResearchQuestionRead])
def list_questions(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    items = db.scalars(
        select(ResearchQuestion).options(selectinload(ResearchQuestion.gap_links))
        .where(ResearchQuestion.project_id == project.id).order_by(ResearchQuestion.created_at)
    ).all()
    return [_question_read(item) for item in items]


@router.post("/research-questions", response_model=ResearchQuestionRead, status_code=status.HTTP_201_CREATED)
def create_question(payload: ResearchQuestionIn, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_selected_gaps(db, project, payload.gap_ids)
    item = ResearchQuestion(project_id=project.id, text=payload.text.strip(), type=payload.type.strip(), created_by=str(user.id))
    db.add(item)
    try:
        db.flush()
        db.add_all(ResearchQuestionGap(project_id=project.id, research_question_id=item.id, gap_id=gap_id) for gap_id in payload.gap_ids)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A matching research question already exists") from exc
    audit.record(db, actor=audit.user_actor(user), action="research_question.created", project_id=project.id,
                 payload={"research_question_id": str(item.id), "gap_count": len(payload.gap_ids)})
    db.commit()
    return _question_read(_question(db, project, item.id))


@router.patch("/research-questions/{question_id}", response_model=ResearchQuestionRead)
def update_question(question_id: uuid.UUID, payload: ResearchQuestionPatch, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _question(db, project, question_id)
    if payload.text is not None:
        item.text = payload.text.strip()
    if payload.type is not None:
        item.type = payload.type.strip()
    if payload.gap_ids is not None:
        _require_selected_gaps(db, project, payload.gap_ids)
        item.gap_links.clear()
        db.flush()
        item.gap_links.extend(ResearchQuestionGap(project_id=project.id, gap_id=gap_id) for gap_id in payload.gap_ids)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A matching research question already exists") from exc
    audit.record(db, actor=audit.user_actor(user), action="research_question.updated", project_id=project.id,
                 payload={"research_question_id": str(item.id), "gap_count": len(item.gap_links)})
    db.commit()
    return _question_read(_question(db, project, item.id))


@router.get("/research-questions/{question_id}/finer", response_model=FinerCritique)
def critique_question(question_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    item = _question(db, project, question_id)
    linked = len(item.gap_links)
    return FinerCritique(
        research_question_id=item.id,
        linked_gap_count=linked,
        items=[
            FinerItem(criterion="feasible", status="needs_human_review", guidance="Assess data, time, skills, and access before proceeding."),
            FinerItem(criterion="interesting", status="needs_human_review", guidance="Confirm that the question matters to the intended scholarly community."),
            FinerItem(criterion="novel", status="linked_to_selected_gaps" if linked else "missing_gap_link", guidance="Use the linked, selected literature gaps as evidence; assess novelty yourself."),
            FinerItem(criterion="ethical", status="needs_human_review", guidance="Assess participant, data, and institutional ethics before design work."),
            FinerItem(criterion="relevant", status="linked_to_selected_gaps" if linked else "missing_gap_link", guidance="Confirm the selected gaps make the question relevant to the project aim."),
        ],
        disclaimer="This checklist is advisory only. It does not validate scholarship, approve a gate, or replace human judgment.",
    )


@router.get("/hypotheses", response_model=list[HypothesisRead])
def list_hypotheses(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return list(db.scalars(select(Hypothesis).where(Hypothesis.project_id == project.id).order_by(Hypothesis.created_at)))


@router.post("/hypotheses", response_model=HypothesisRead, status_code=status.HTTP_201_CREATED)
def create_hypothesis(payload: HypothesisIn, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _question(db, project, payload.research_question_id)
    item = Hypothesis(project_id=project.id, created_by=str(user.id), **payload.model_dump())
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A matching hypothesis already exists") from exc
    audit.record(db, actor=audit.user_actor(user), action="hypothesis.created", project_id=project.id,
                 payload={"hypothesis_id": str(item.id), "research_question_id": str(item.research_question_id)})
    db.commit()
    db.refresh(item)
    return item


@router.patch("/hypotheses/{hypothesis_id}", response_model=HypothesisRead)
def update_hypothesis(hypothesis_id: uuid.UUID, payload: HypothesisPatch, project: Project = Depends(project_access(WRITE_ROLES)), user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _hypothesis(db, project, hypothesis_id)
    values = payload.model_dump(exclude_none=True)
    if "research_question_id" in values:
        _question(db, project, values["research_question_id"])
    for key, value in values.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A matching hypothesis already exists") from exc
    audit.record(db, actor=audit.user_actor(user), action="hypothesis.updated", project_id=project.id,
                 payload={"hypothesis_id": str(item.id), "research_question_id": str(item.research_question_id)})
    db.commit()
    db.refresh(item)
    return item
