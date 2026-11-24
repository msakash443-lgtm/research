"""The `figure_correct` task body (plan M8.7): the AI proposes a corrected spec for one figure version that fails its checks.

Mirrors `figure_accuracy_task.py`. The corrected spec is stored as a pending `AiSuggestion` (child of the version's
generating suggestion) only when it passes the deterministic checks with no ERROR finding. A refused or failed
correction writes an `ai.failed` audit event with a stable code and stores nothing. Nothing is auto-accepted.
"""

from __future__ import annotations

import json
import uuid

from app import audit, figure_checks, figure_correction, figure_generation, figure_spec, figure_store, figure_suggestions
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import AIOffError, llm_for_project
from app.config import get_settings
from app.database import SessionLocal
from app.figure_correction import CorrectionError
from app.figure_generation import FigureGenerationError
from app.fulltext import FullTextError
from app.models import Figure, Project, UploadedDocument
from app.prompt_registry import load_prompt
from app.suggestion_store import create_suggestion
from app.task_registry import ClaimedTask, PermanentTaskError
from app.uploaded_documents import error_code
from app.workspace_ai_task import _record_failure

PRODUCER = "figure_correct"
ACTION = "correct"
PURPOSE = "figure_correct"

# Module-level (name, version) constant: the evaluation rule (evals/baselines.py) finds prompts by this.
FIGURE_CORRECTION_PROMPT = figure_correction.PROMPT


def scrub_figure_correct_payload(payload: dict) -> dict:
    """The payload kept once the task has ended: ids and the actor only, so nothing is removed."""
    return dict(payload)


def _fail(db, project: Project, actor: str, code: str) -> None:
    _record_failure(db, project, actor, ACTION, code)
    raise PermanentTaskError(f"figure correction stopped: {code}")


def run_figure_correct(payload: dict, task: ClaimedTask) -> None:
    """The body of the `figure_correct` task. Raises `PermanentTaskError` on any refusal or failure."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        figure_id = uuid.UUID(str(payload["figure_id"]))
        number = int(payload["number"])
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A figure_correct task needs project_id, figure_id and number.") from exc

    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        figure = db.get(Figure, figure_id)
        if project is None or figure is None or figure.project_id != project.id:
            raise PermanentTaskError("The figure for this correction no longer exists.")
        version = figure_store.version_by_number(db, figure, number)
        if version is None:
            raise PermanentTaskError("The figure version for this correction no longer exists.")
        document = db.get(UploadedDocument, figure.uploaded_document_id)
        if document is None or document.withdrawn_at is not None:
            _fail(db, project, actor, "document_withdrawn")
        try:
            full = figure_store.document_full_text(settings, document)
        except FullTextError as exc:
            _fail(db, project, actor, error_code(exc))

        system_prompt = load_prompt(*FIGURE_CORRECTION_PROMPT)
        try:
            findings = figure_checks.check_figure(version.spec_json, full, figure.kind)
            draft = figure_correction.draft(version.spec_json, findings)
            fenced = figure_generation.fence(figure_generation.passages(full, figure.kind))
            llm = llm_for_project(settings, project, purpose=PURPOSE)
            reply = llm.complete_json(
                system_prompt.system,
                system_prompt.render_user(document=fenced.block, draft=draft),
                figure_spec.schema(figure.kind),
            )
            spec = figure_generation.check_reply(
                reply, full, kind=figure.kind, system=system_prompt.system, nonce=fenced.nonce,
            )
            figure_correction.verify(spec, full, figure.kind)
        except AIOffError:
            _fail(db, project, actor, "ai_off")
        except LLMConfigurationError:
            _fail(db, project, actor, "llm_not_configured")
        except LLMResponseError:
            _fail(db, project, actor, "llm_response_invalid")
        except FigureGenerationError as exc:
            _fail(db, project, actor, exc.code if exc.code == "leaked_prompt" else figure_correction.CODE)
        except CorrectionError as exc:
            _fail(db, project, actor, exc.code)

        task.ensure_owned(db)
        suggestion = create_suggestion(
            db,
            project_id=project.id,
            target_kind=figure_suggestions.TARGET_KIND,
            target_id=str(figure.id),
            target_field="spec",
            content=json.dumps(spec, sort_keys=True),
            model_id=llm._model,
            prompt_version=system_prompt.ref,
            producing_agent=PRODUCER,
            action=ACTION,
            input_hash=version.spec_hash,
            input_chars=len(fenced.block),
            parent_id=version.suggestion_id,
        )
        audit.record(
            db, actor=actor, action="ai.proposed", project_id=project.id,
            payload={
                "action": ACTION, "target_kind": figure_suggestions.TARGET_KIND, "target_id": str(figure.id),
                "suggestion_id": str(suggestion.id), "proposed": 1,
            },
            model_id=llm._model, prompt_version=system_prompt.ref,
        )
        db.commit()
    finally:
        db.close()
