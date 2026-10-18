"""Item-quality checks (M4.4.1). Stateless: the items sent are checked and not stored.

The flags are prompts for the researcher to re-read an item; nothing here changes an instrument or a gate.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import READ_ROLES, project_access
from app.item_checks import check_item
from app.measure_library import MEASURES, build_draft
from app.models import Project
from app.validity_plan import build_plan

router = APIRouter(prefix="/projects/{project_id}/instrument", tags=["instrument"])

MAX_ITEMS = 200


class ItemChecksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[str] = Field(min_length=1, max_length=MAX_ITEMS)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure_keys: list[str] = Field(min_length=1, max_length=len(MEASURES))
    own_items: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)


class ValidityPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure_keys: list[str] = Field(default_factory=list, max_length=len(MEASURES))
    own_item_count: int = Field(default=0, ge=0, le=MAX_ITEMS)


@router.post("/validity-plan")
def validity_plan(body: ValidityPlanIn, project: Project = Depends(project_access(READ_ROLES))) -> dict[str, Any]:
    """Stateless template of reliability and validity evidence to plan for. Nothing is stored; no gate is touched."""
    try:
        return build_plan(body.measure_keys, body.own_item_count)
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown measure key: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/measures")
def list_measures(project: Project = Depends(project_access(READ_ROLES))) -> dict[str, Any]:
    """The owner-approved shortlist (citation metadata only; licences all 'to check')."""
    return {
        "note": "Citations only. Item wording is not stored here; check each licence before using any item.",
        "measures": [
            {"key": m.key, "name": m.name, "construct": m.construct, "citation": m.citation, "doi": m.doi, "pmid": m.pmid, "licence": m.licence}
            for m in MEASURES
        ],
    }


@router.post("/draft")
def draft_instrument(body: DraftIn, project: Project = Depends(project_access(READ_ROLES))) -> dict[str, Any]:
    """Stateless: assembles the references and the scholar's own items. Nothing is stored; no gate is touched."""
    try:
        return build_draft(body.measure_keys, [item.strip() for item in body.own_items if item.strip()])
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown measure key: {exc.args[0]}") from exc


@router.post("/item-checks")
def item_checks(body: ItemChecksIn, project: Project = Depends(project_access(READ_ROLES))) -> dict[str, Any]:
    results = []
    for index, text in enumerate(body.items):
        try:
            result = check_item(text)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"item {index}: {exc}") from exc
        results.append(
            {
                "index": index,
                "text": result.text,
                "flags": [{"kind": f.kind, "matched": f.matched, "reason": f.reason} for f in result.flags],
            }
        )
    return {
        "stored": False,
        "note": "Heuristic flags only. Re-read each flagged item; the researcher decides whether to change it.",
        "items": results,
    }
