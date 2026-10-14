"""The request checks and the task body for the AI side panel (X.31.11).

`check_request` is the one place that decides whether a side-panel action may run. The router calls it before
queueing (so the researcher gets a clear answer at once) and the task calls it again before sending anything
(so a level or lock change made while the task waited still stops it).

Failures are recorded: a refused or failed run writes an `ai.failed` audit event with a stable code and no text,
and the task fails loudly. A failed run never stores a suggestion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import AIOffError, llm_for_project
from app.ai_item_locks import is_locked
from app.config import get_settings
from app.database import SessionLocal
from app.models import Document, Note, Project, Source, SourceExcerpt as ExcerptRow
from app.suggestion_store import create_suggestion
from app.task_registry import PermanentTaskError
from app.workspace_ai import (
    MAX_SOURCES,
    SourceExcerpt,
    WorkspaceAIError,
    ensure_action,
    input_hash,
    level_allows,
    validate_text,
)
from app.workspace_ai_run import run_action

DOCUMENT_KIND = "document"  # matches routers/documents.py AI_TARGET_KIND
NOTE_KIND = "note"
PRODUCER = "workspace_ai"


class Refused(Exception):
    """The request may not run. `code` is stable; `status_code` is the HTTP answer the router gives."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Target:
    kind: str
    id: uuid.UUID


def check_request(
    db: Session,
    project: Project,
    action: str,
    *,
    document_id: uuid.UUID | None,
    note_id: uuid.UUID | None,
) -> Target:
    """Raise `Refused` unless this action may run on this target now. Returns the target otherwise."""
    involvement = project.ai_involvement.value
    if involvement == "off":
        raise Refused("ai_off", "AI is turned off for this project, so nothing was sent to a model.")
    if not level_allows(involvement, action):
        raise Refused("ai_level_too_low", "Your project's AI level is Suggestions only, which does not allow rewording sentences.")
    if (document_id is None) == (note_id is None):
        raise Refused("bad_target", "Choose either a section or a note for this request.", status_code=422)
    if document_id is not None:
        document = db.get(Document, document_id)
        if document is None or document.project_id != project.id:
            raise Refused("not_found", "That section is not in this project.", status_code=404)
        if document.ai_locked or is_locked(db, project.id, DOCUMENT_KIND, document.id):
            raise Refused("ai_locked", "This section is marked 'never send to AI', so nothing was sent.")
        target = Target(DOCUMENT_KIND, document.id)
    else:
        note = db.get(Note, note_id)
        if note is None or note.project_id != project.id:
            raise Refused("not_found", "That note is not in this project.", status_code=404)
        if note.ai_locked:
            raise Refused("ai_locked", "This note is marked 'never send to AI', so nothing was sent.")
        target = Target(NOTE_KIND, note.id)
    if action == "find_evidence" and not project_excerpts(db, project):
        raise Refused("no_sources", "This project has no source excerpts yet, so there is nothing to compare with.")
    return target


def project_excerpts(db: Session, project: Project) -> list[SourceExcerpt]:
    """One excerpt per source, for the first `MAX_SOURCES` sources that have one. Verification is carried along."""
    sources = db.scalars(
        select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None)).order_by(Source.created_at, Source.id)
    ).all()
    found: list[SourceExcerpt] = []
    for source in sources:
        if len(found) >= MAX_SOURCES:
            break
        excerpt = db.scalars(
            select(ExcerptRow).where(ExcerptRow.source_id == source.id).order_by(ExcerptRow.created_at, ExcerptRow.id)
        ).first()
        if excerpt is None:
            continue
        found.append(
            SourceExcerpt(
                source_id=str(source.id),
                excerpt_id=str(excerpt.id),
                title=source.title,
                verified=bool(source.metadata_verified),
                text=excerpt.content,
            )
        )
    return found


def scrub_action_payload(payload: dict) -> dict:
    """The task payload without the researcher's selected text, kept once the task has ended (X.31.11.3)."""
    return {key: value for key, value in payload.items() if key != "text"}


def _optional_uuid(value: object) -> uuid.UUID | None:
    return None if value in (None, "") else uuid.UUID(str(value))


def _record_failure(db: Session, project: Project, actor: str, action: str, code: str) -> None:
    db.rollback()  # nothing from this run is kept; only the record that it failed
    audit.record(db, actor=actor, action="ai.failed", project_id=project.id, payload={"action": action, "code": code})
    db.commit()


def run_workspace_ai_action(payload: dict) -> None:
    """The body of the `workspace_ai_action` task. Raises `PermanentTaskError` on any refusal or failure."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        action = ensure_action(str(payload["action"]))
        text = validate_text(str(payload["text"]))
        document_id = _optional_uuid(payload.get("document_id"))
        note_id = _optional_uuid(payload.get("note_id"))
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A workspace_ai_action task needs project_id, action, text and a document_id or note_id.") from exc

    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this AI request no longer exists.")
        try:
            target = check_request(db, project, action, document_id=document_id, note_id=note_id)
        except Refused as exc:
            _record_failure(db, project, actor, action, exc.code)
            raise PermanentTaskError(exc.message) from exc

        sources = project_excerpts(db, project) if action == "find_evidence" else []
        try:
            llm = llm_for_project(settings, project, purpose=f"workspace_{action}")
            result = run_action(llm, action, text, sources)
        except AIOffError as exc:
            _record_failure(db, project, actor, action, "ai_off")
            raise PermanentTaskError(str(exc)) from exc
        except LLMConfigurationError as exc:
            _record_failure(db, project, actor, action, "llm_not_configured")
            raise PermanentTaskError(str(exc)) from exc
        except (LLMResponseError, WorkspaceAIError) as exc:
            _record_failure(db, project, actor, action, getattr(exc, "code", "llm_failed"))
            raise PermanentTaskError(str(exc)) from exc

        digest = input_hash(text)
        for item in result.items:
            create_suggestion(
                db,
                project_id=project.id,
                target_kind=target.kind,
                target_id=str(target.id),
                content=item.content,
                model_id=result.model_id,
                prompt_version=result.prompt_version,
                producing_agent=PRODUCER,
                evidence=[item.evidence] if item.evidence else None,
                action=action,
                input_hash=digest,
                input_chars=len(text),
            )
        audit.record(
            db, actor=actor, action="ai.proposed", project_id=project.id,
            payload={"action": action, "target_kind": target.kind, "target_id": str(target.id), "proposed": len(result.items)},
            model_id=result.model_id, prompt_version=result.prompt_version,
        )
        db.commit()
    finally:
        db.close()
