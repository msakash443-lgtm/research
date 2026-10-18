"""Ethics pack (M4.5). Read-only and stateless: the pack is generated on request, nothing is stored.

Approving ethics is the owner's G7 decision; this endpoint does not change any gate.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app import ethics_pack
from app.dependencies import READ_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/ethics-pack", tags=["ethics"])


@router.get("")
def get_ethics_pack(
    research_question: str | None = Query(None, max_length=2000),
    project: Project = Depends(project_access(READ_ROLES)),
) -> dict[str, Any]:
    documents = ethics_pack.build_ethics_pack(project.title, project.description, research_question)
    return {
        "gate": "G7",
        "note": (
            "Templates only. Bracketed fields must be completed by the researcher. "
            "The owner approves ethics through gate G7; nothing here approves it."
        ),
        "documents": [{"key": d.key, "title": d.title, "body": d.body} for d in documents],
    }
