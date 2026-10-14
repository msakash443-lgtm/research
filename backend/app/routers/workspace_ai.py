"""AI side panel for the writing workspace (X.31.11): request an action on a section or note, or preview what it would do.

Requesting only queues a task. The task (`workspace_ai_task.py`) checks everything again before anything is sent,
and stores proposals as shared `AiSuggestion` rows (X.46). Accepting, editing or rejecting a proposal uses the
shared endpoints under `/projects/{id}/suggestions`; nothing here changes the researcher's own text.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.dependencies import WRITE_ROLES, current_user, project_access
from app.models import Project, User
from app.task_handlers import WORKSPACE_AI_ACTION
from app.task_queue import enqueue_task
from app.workspace_ai import MAX_SOURCES, validate_text
from app.workspace_ai_task import Refused, check_request, project_excerpts

router = APIRouter(prefix="/projects/{project_id}/ai", tags=["workspace ai"])

ActionName = Literal["challenge", "find_evidence", "clearer_sentence", "argument_check"]


class WorkspaceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ActionName
    text: str = Field(min_length=1, max_length=5000)  # the selected text; the exact cap is applied by validate_text
    document_id: uuid.UUID | None = None
    note_id: uuid.UUID | None = None

    @field_validator("text")
    @classmethod
    def _text_is_usable(cls, value: str) -> str:
        return validate_text(value)


def _refuse(exc: Refused) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("/suggestions", status_code=202)
def request_action(
    body: WorkspaceActionRequest,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue one side-panel action on the selected text. Refused before queueing if the project's AI level or a lock forbids it."""
    try:
        target = check_request(db, project, body.action, document_id=body.document_id, note_id=body.note_id)
    except Refused as exc:
        raise _refuse(exc) from exc
    actor = audit.user_actor(user)
    task = enqueue_task(
        db,
        project.id,
        WORKSPACE_AI_ACTION,
        payload={
            "project_id": str(project.id),
            "action": body.action,
            "text": body.text,
            "document_id": str(body.document_id) if body.document_id else None,
            "note_id": str(body.note_id) if body.note_id else None,
            "actor": actor,
        },
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="ai.requested", project_id=project.id,
        payload={
            "task_id": str(task.id), "action": body.action, "target_kind": target.kind,
            "target_id": str(target.id), "input_chars": len(body.text),
        },
    )
    db.commit()
    return {"task_id": str(task.id), "status": task.status.value}


@router.post("/suggestions/preview")
def preview_action(
    body: WorkspaceActionRequest,
    project: Project = Depends(project_access(WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    """What the action would do, without sending anything: where the text goes and how much. Nothing is stored."""
    try:
        target = check_request(db, project, body.action, document_id=body.document_id, note_id=body.note_id)
    except Refused as exc:
        raise _refuse(exc) from exc
    sources = project_excerpts(db, project) if body.action == "find_evidence" else []
    route = project.ai_model_route.value
    return {
        "action": body.action,
        "target_kind": target.kind,
        "words": len(body.text.split()),
        "characters": len(body.text),
        "sources_sent": min(len(sources), MAX_SOURCES),
        "model_route": route,
        "sends_to": "the local model only" if route == "local" else "the project's standard model",
        "note": "Nothing has been sent. Accept, edit or reject what comes back; your own text is not changed.",
    }
