"""Worker handler for M6.10 front-matter proposals."""

from __future__ import annotations

import uuid

from app import audit
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import AIOffError, llm_for_project
from app.config import get_settings
from app.database import SessionLocal
from app.front_matter import FrontMatterError, build
from app.front_matter_run import run
from app.models import Document, ManuscriptOutput, Project
from app.similarity_task import latest_version
from app.suggestion_store import create_suggestion
from app.task_registry import PermanentTaskError


def run_front_matter(task, payload: dict) -> None:
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        document_id = uuid.UUID(str(payload["document_id"]))
        source_version_id = uuid.UUID(str(payload["source_version_id"]))
        kind = str(payload["kind"])
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("front-matter task payload is invalid") from exc
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        document = db.get(Document, document_id)
        if project is None or document is None or document.project_id != project.id:
            raise PermanentTaskError("document is not in the requested project")
        version = latest_version(db, document.id)
        if version is None:
            raise PermanentTaskError("a saved document version is required")
        if version.id != source_version_id:
            raise PermanentTaskError("the document changed after this front-matter request; request it again")
        task.ensure_owned(db)
        try:
            result = run(
                llm_for_project(get_settings(), project, purpose=f"front_matter:{kind}"),
                kind,
                version.content_text,
            )
        except (AIOffError, LLMConfigurationError, LLMResponseError, FrontMatterError) as exc:
            audit.record(
                db, actor=actor, action="ai.failed", project_id=project.id,
                payload={"action": "front_matter", "kind": kind, "document_id": str(document.id),
                         "reason": type(exc).__name__},
            )
            db.commit()
            raise PermanentTaskError(str(exc)) from exc
        task.ensure_owned(db)
        output = build(kind, result.text, source_version_id=str(version.id), source_content_json=version.content_json)
        db.add(ManuscriptOutput(
            project_id=project.id, document_id=document.id, source_version_id=version.id,
            kind=output.kind, content_text=output.text, content_hash=output.source_hash,
            created_by=actor, model_id=result.model_id, prompt_version=result.prompt_version,
        ))
        create_suggestion(
            db, project_id=project.id, target_kind="front_matter", target_id=str(document.id),
            target_field=kind, content=result.text, model_id=result.model_id,
            prompt_version=result.prompt_version, producing_agent="front_matter", action=kind,
            input_chars=len(version.content_text),
        )
        audit.record(
            db, actor=actor, action="manuscript.front_matter_proposed", project_id=project.id,
            payload={"kind": kind, "document_id": str(document.id), "source_version_id": str(version.id)},
            model_id=result.model_id, prompt_version=result.prompt_version,
        )
        db.commit()
    finally:
        db.close()
