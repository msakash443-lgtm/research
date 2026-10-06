"""Append audit events for every state change.

`record()` only adds the row to the caller's session, so the event commits (or rolls
back) together with the change it describes. Payloads hold ids, counts and statuses
only: never secrets, prompts, model answers or source text.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, User

AGENT_RESEARCH_RUN = "agent:research-run"
AGENT_ARC_RETRIEVAL = "agent:arc-retrieval"
SYSTEM_WORKER = "agent:worker"


def user_actor(user: User) -> str:
    return str(user.id)


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    project_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    model_id: str | None = None,
    prompt_version: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        project_id=project_id,
        actor=actor,
        action=action,
        payload_json=payload,
        model_id=model_id,
        prompt_version=prompt_version,
    )
    db.add(event)
    return event
