"""Method recommender and sample-size calculators (M4.3). Read-only and stateless: nothing is stored."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import design_tools, saturation_guidance
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/design", tags=["design"])


@router.get("/method-recommendation")
def method_recommendation(
    goal: str = Query(...),
    can_randomize: bool | None = Query(None),
    accessible_population: bool | None = Query(None),
    project: Project = Depends(project_access(READ_ROLES)),
) -> dict[str, Any]:
    try:
        return design_tools.recommend_method(goal, can_randomize=can_randomize, accessible_population=accessible_population)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/saturation-guidance")
def saturation(
    design: str = Query(...),
    homogeneous: bool | None = Query(None),
    narrow_objective: bool | None = Query(None),
    project: Project = Depends(project_access(READ_ROLES)),
) -> dict[str, Any]:
    try:
        return saturation_guidance.saturation_guidance(design, homogeneous=homogeneous, narrow_objective=narrow_objective)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/sample-size")
def sample_size(
    test: str = Query(...),
    d: float | None = Query(None),
    p1: float | None = Query(None),
    p2: float | None = Query(None),
    r: float | None = Query(None),
    margin: float | None = Query(None),
    confidence: float | None = Query(None),
    p: float | None = Query(None),
    population: int | None = Query(None),
    f: float | None = Query(None),
    groups: int | None = Query(None),
    f2: float | None = Query(None),
    predictors: int | None = Query(None),
    alpha: float | None = Query(None),
    power: float | None = Query(None),
    sides: int | None = Query(None),
    attrition: float = Query(0.0),
    project: Project = Depends(project_access(READ_ROLES)),
) -> dict[str, Any]:
    values = {
        "d": d, "p1": p1, "p2": p2, "r": r, "margin": margin, "confidence": confidence,
        "p": p, "population": population,
        "f": f, "groups": groups, "f2": f2, "predictors": predictors, "alpha": alpha, "power": power, "sides": sides,
    }
    allowed = {
        "two_means": {"d", "alpha", "power", "sides"},
        "one_mean": {"d", "alpha", "power", "sides"},
        "two_proportions": {"p1", "p2", "alpha", "power", "sides"},
        "correlation": {"r", "alpha", "power", "sides"},
        "survey_proportion": {"margin", "confidence", "p", "population"},
        "anova_oneway": {"f", "groups", "alpha", "power"},
        "regression": {"f2", "predictors", "alpha", "power"},
    }
    if test not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"test must be one of: {', '.join(design_tools.TESTS)}")
    stray = sorted(k for k, v in values.items() if v is not None and k not in allowed[test])
    if stray:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{test} does not use: {', '.join(stray)}")
    try:
        return design_tools.sample_size(test, values, attrition)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
