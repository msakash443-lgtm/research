"""Qualitative transcript anonymisation (M5.7.3, spec 5.8.5). Stateless: the redacted text is returned, not stored.

The researcher lists the terms to remove; nothing is detected automatically. Transcription (speech-to-text) is not
here: it needs a service that has been approved for participant audio (M5.7.4).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app import transcripts
from app.dependencies import WRITE_ROLES, project_access
from app.models import Project

router = APIRouter(prefix="/projects/{project_id}/transcripts", tags=["transcripts"])

MAX_TEXT_CHARS = 500_000


class AnonymiseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    terms: list[str] = Field(min_length=1, max_length=transcripts.MAX_TERMS)


@router.post("/anonymise")
def anonymise(
    body: AnonymiseIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
) -> dict[str, Any]:
    try:
        text, log = transcripts.redact(body.text, body.terms)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        "text": text,
        "log": log,
        "note": "Only the listed terms were removed. Review the text for other identifiers before it leaves the project.",
    }
