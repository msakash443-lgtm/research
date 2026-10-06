from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app.criteria import (
    CODE_PREFIX,
    CODE_RE,
    FRAMEWORKS,
    MAX_CRITERIA,
    MAX_TEXT,
    MIN_TEXT,
    criteria_locked,
    problems,
)
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import Project, ScreeningCriterion, User

router = APIRouter(prefix="/projects/{project_id}/criteria", tags=["criteria"])


class CriterionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    text: str = Field(min_length=1, max_length=MAX_TEXT * 2)
    element: str | None = Field(default=None, max_length=40)
    code: str | None = Field(default=None, max_length=10)  # keep a code to keep citing it; omit to get the next free one

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in CODE_PREFIX:
            raise ValueError("kind must be 'include' or 'exclude'")
        return value

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        text = " ".join(value.split())
        if not MIN_TEXT <= len(text) <= MAX_TEXT:
            raise ValueError(f"A criterion must be {MIN_TEXT}-{MAX_TEXT} characters")
        return text

    @model_validator(mode="after")
    def _code_matches_kind(self) -> "CriterionIn":
        if self.code is not None and (not CODE_RE.match(self.code) or self.code[0] != CODE_PREFIX[self.kind]):
            prefix = CODE_PREFIX[self.kind]
            raise ValueError(f"A {self.kind} code looks like {prefix}1, {prefix}2, ...")
        return self


class CriteriaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str
    criteria: list[CriterionIn] = Field(max_length=MAX_CRITERIA)

    @field_validator("framework")
    @classmethod
    def _framework(cls, value: str) -> str:
        if value not in FRAMEWORKS:
            raise ValueError("framework must be one of: " + ", ".join(FRAMEWORKS))
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "CriteriaUpdate":
        elements = FRAMEWORKS[self.framework]
        for item in self.criteria:
            if item.element is not None and item.element not in elements:
                allowed = ", ".join(elements) or "none (custom has no elements)"
                raise ValueError(f"element {item.element!r} is not part of {self.framework}; allowed: {allowed}")
        codes = [c.code for c in self.criteria if c.code]
        if len(codes) != len(set(codes)):
            raise ValueError("Criterion codes must be unique")
        texts = [(c.kind, c.text.casefold()) for c in self.criteria]
        if len(texts) != len(set(texts)):
            raise ValueError("The same criterion is listed twice")
        return self


class CriterionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    kind: str
    text: str
    element: str | None
    created_by: str | None


class CriteriaRead(BaseModel):
    framework: str | None
    elements: list[str]  # the framework's elements, for building the form
    locked: bool  # G2 approved by a person: criteria can't change until it is reopened
    problems: list[str]  # what is still missing before G2 (advice only)
    criteria: list[CriterionRead]


def _rows(db: Session, project: Project) -> list[ScreeningCriterion]:
    return list(
        db.scalars(
            select(ScreeningCriterion).where(ScreeningCriterion.project_id == project.id).order_by(ScreeningCriterion.position)
        )
    )


def _view(db: Session, project: Project) -> CriteriaRead:
    rows = _rows(db, project)
    return CriteriaRead(
        framework=project.criteria_framework,
        elements=list(FRAMEWORKS.get(project.criteria_framework or "", ())),
        locked=criteria_locked(db, project),
        problems=problems(project.criteria_framework, rows),
        criteria=[CriterionRead.model_validate(r) for r in rows],
    )


@router.get("", response_model=CriteriaRead)
def get_criteria(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    return _view(db, project)


@router.put("", response_model=CriteriaRead)
def set_criteria(
    payload: CriteriaUpdate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace the project's framework and criteria. Locked once a person has approved gate G2."""
    if criteria_locked(db, project):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gate G2 is approved for these criteria. Reopen the project at the search-planning stage to change them.",
        )
    existing = {c.code: c for c in _rows(db, project)}
    used = {c.code for c in payload.criteria if c.code}
    # New codes continue after the highest one this project has, so a code that was dropped is not
    # handed to a different criterion later (decisions that cite it would silently change meaning).
    counters = {kind: max([int(c[1:]) for c in existing if c[0] == prefix] or [0]) for kind, prefix in CODE_PREFIX.items()}

    def next_code(kind: str) -> str:
        while True:
            counters[kind] += 1
            code = f"{CODE_PREFIX[kind]}{counters[kind]}"
            if code not in used:
                used.add(code)
                return code

    before = sorted(existing)
    db.execute(delete(ScreeningCriterion).where(ScreeningCriterion.project_id == project.id))
    db.flush()
    actor = audit.user_actor(user)
    after = []
    for position, item in enumerate(payload.criteria):
        previous = existing.get(item.code) if item.code else None
        code = item.code or next_code(item.kind)
        after.append(code)
        db.add(
            ScreeningCriterion(
                project_id=project.id,
                kind=item.kind,
                code=code,
                text=item.text,
                element=item.element,
                position=position,
                created_by=previous.created_by if previous else actor,  # keep who first wrote a kept criterion
            )
        )
    project.criteria_framework = payload.framework
    project.touch()
    audit.record(
        db, actor=actor, action="criteria.updated", project_id=project.id,
        payload={"framework": payload.framework, "codes_before": before, "codes_after": sorted(after)},
    )
    db.commit()
    return _view(db, project)
