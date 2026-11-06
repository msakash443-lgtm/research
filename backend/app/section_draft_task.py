"""The request checks and the task body for section drafting (X.46.8 / M6.1.a).

Mirrors `workspace_ai_task.py`: `check_draft_request` is the one place that decides whether a draft may run on
a section. The router calls it before queueing (so the researcher gets a clear answer at once) and the task
calls it again before sending anything (so a level or lock change made while the task waited still stops it).

Per Q27 a draft is always a suggestion a person must accept: this never writes to the document itself. A
refused or failed run writes an `ai.failed` audit event with a stable code and no text, and never stores a
suggestion.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import AIOffError, llm_for_project
from app.ai_item_locks import is_locked
from app.config import get_settings
from app.database import SessionLocal
from app.models import Claim, Document, Project
from app.section_draft import OutlineClaim, SectionDraftError
from app.section_draft_run import run_draft
from app.suggestion_store import create_suggestion
from app.task_registry import PermanentTaskError
from app.workspace_ai_task import DOCUMENT_KIND, Refused, _record_failure

PRODUCER = "section_draft"
ACTION = "draft"


def check_draft_request(db: Session, project: Project, document_id: uuid.UUID) -> Document:
    """Raise `Refused` unless a section draft may run on this document now. Returns the document otherwise.

    Available at every AI level above Off: per Q27 this is a suggestion like `challenge` or `find_evidence`,
    never a reason to require Full, and never a way to bypass the suggestion step even at Full.
    """
    involvement = project.ai_involvement.value
    if involvement == "off":
        raise Refused("ai_off", "AI is turned off for this project, so nothing was sent to a model.")
    document = db.get(Document, document_id)
    if document is None or document.project_id != project.id:
        raise Refused("not_found", "That section is not in this project.", status_code=404)
    if document.ai_locked or is_locked(db, project.id, DOCUMENT_KIND, document.id):
        raise Refused("ai_locked", "This section is marked 'never send to AI', so nothing was sent.")
    return document


def section_outline(db: Session, document: Document) -> list[OutlineClaim]:
    """The claims bound to this section, oldest first (mirrors `routers/documents.py::section_outline`)."""
    claims = db.scalars(
        select(Claim)
        .where(Claim.project_id == document.project_id, Claim.section_id == document.id)
        .order_by(Claim.created_at, Claim.id)
    ).all()
    return [OutlineClaim(claim_id=str(claim.id), text=claim.text, verified=claim.verified) for claim in claims]


def scrub_draft_payload(payload: dict) -> dict:
    """The task payload kept once the task has ended (X.31.11.3-style convention): nothing sensitive is sent
    in a section_draft payload (the claim text itself is read fresh from the database, not carried in it)."""
    return dict(payload)


def _optional_uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def run_section_draft(payload: dict) -> None:
    """The body of the `section_draft` task. Raises `PermanentTaskError` on any refusal or failure."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        document_id = _optional_uuid(payload["document_id"])
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A section_draft task needs project_id and document_id.") from exc

    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this draft request no longer exists.")
        try:
            document = check_draft_request(db, project, document_id)
        except Refused as exc:
            _record_failure(db, project, actor, ACTION, exc.code)
            raise PermanentTaskError(exc.message) from exc

        claims = section_outline(db, document)
        if not claims:
            _record_failure(db, project, actor, ACTION, "empty_outline")
            raise PermanentTaskError("This section has no claims bound to it yet, so there is nothing to draft from.")

        try:
            llm = llm_for_project(settings, project, purpose="section_draft")
            result = run_draft(llm, claims)
        except AIOffError as exc:
            _record_failure(db, project, actor, ACTION, "ai_off")
            raise PermanentTaskError(str(exc)) from exc
        except LLMConfigurationError as exc:
            _record_failure(db, project, actor, ACTION, "llm_not_configured")
            raise PermanentTaskError(str(exc)) from exc
        except (LLMResponseError, SectionDraftError) as exc:
            _record_failure(db, project, actor, ACTION, getattr(exc, "code", "llm_failed"))
            raise PermanentTaskError(str(exc)) from exc

        create_suggestion(
            db,
            project_id=project.id,
            target_kind=DOCUMENT_KIND,
            target_id=str(document.id),
            target_field="draft",
            content=result.draft,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            producing_agent=PRODUCER,
            action=ACTION,
            input_chars=sum(len(claim.text) for claim in claims),
        )
        audit.record(
            db, actor=actor, action="ai.proposed", project_id=project.id,
            payload={"action": ACTION, "target_kind": DOCUMENT_KIND, "target_id": str(document.id), "proposed": 1},
            model_id=result.model_id, prompt_version=result.prompt_version,
        )
        db.commit()
    finally:
        db.close()
