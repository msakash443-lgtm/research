"""The `figure_generate` task body (plan M8.4): draft a figure spec from an uploaded paper as a suggestion.

Mirrors `section_draft_task.py`. A refused or failed run writes an `ai.failed` audit event with a stable code and
no text, and stores nothing. A valid reply becomes one pending suggestion on the figure (target kind `figure`,
field `spec`); a person must accept it (`figure_suggestions`) before any version is written.
"""

from __future__ import annotations

import json
import uuid

from app import audit, figure_generation, figure_spec, figure_suggestions
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import AIOffError, llm_for_project
from app.config import get_settings
from app.database import SessionLocal
from app.figure_generation import FigureGenerationError
from app.fulltext import FullTextError
from app.models import Figure, Project, UploadedDocument
from app.object_storage import get_object_store
from app.prompt_registry import Prompt, load_prompt
from app.suggestion_store import create_suggestion
from app.task_registry import ClaimedTask, PermanentTaskError
from app.uploaded_documents import error_code, extract_text, format_for
from app.workspace_ai_task import _record_failure

PRODUCER = "figure_generate"
ACTION = "generate"
PURPOSE = "figure_generate"

# Module-level (name, version) constants: the evaluation rule (evals/baselines.py) finds prompts by these.
CONCEPTUAL_FRAMEWORK_PROMPT = figure_generation.PROMPTS[figure_spec.CONCEPTUAL_FRAMEWORK]
METHODOLOGY_FLOWCHART_PROMPT = figure_generation.PROMPTS[figure_spec.METHODOLOGY_FLOWCHART]


def _generation_prompt(kind: str) -> Prompt:
    if kind == figure_spec.CONCEPTUAL_FRAMEWORK:
        return load_prompt(*CONCEPTUAL_FRAMEWORK_PROMPT)
    if kind == figure_spec.METHODOLOGY_FLOWCHART:
        return load_prompt(*METHODOLOGY_FLOWCHART_PROMPT)
    raise KeyError(kind)


def scrub_figure_payload(payload: dict) -> dict:
    """The payload kept once the task has ended: it holds only ids and the actor, so nothing is removed."""
    return dict(payload)


def _fail(db, project: Project, actor: str, code: str) -> None:
    _record_failure(db, project, actor, ACTION, code)
    raise PermanentTaskError(f"figure generation stopped: {code}")


def run_figure_generate(payload: dict, task: ClaimedTask) -> None:
    """The body of the `figure_generate` task. Raises `PermanentTaskError` on any refusal or failure."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        figure_id = uuid.UUID(str(payload["figure_id"]))
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A figure_generate task needs project_id and figure_id.") from exc

    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        figure = db.get(Figure, figure_id)
        if project is None or figure is None or figure.project_id != project.id:
            raise PermanentTaskError("The figure for this request no longer exists.")
        document = db.get(UploadedDocument, figure.uploaded_document_id)
        if document is None or document.withdrawn_at is not None:
            _fail(db, project, actor, "document_withdrawn")

        try:
            data = get_object_store(settings).get(document.storage_key)
            full = extract_text(
                format_for(document.media_type),
                data,
                max_pdf_pages=settings.fulltext_max_pages,
                max_docx_paragraphs=settings.uploaded_docx_max_paragraphs,
            )
        except FullTextError as exc:
            _fail(db, project, actor, error_code(exc))

        system_prompt = _generation_prompt(figure.kind)
        fenced = figure_generation.fence(figure_generation.passages(full, figure.kind))
        try:
            llm = llm_for_project(settings, project, purpose=PURPOSE)
            reply = llm.complete_json(
                system_prompt.system,
                system_prompt.render_user(document=fenced.block),
                figure_spec.schema(figure.kind),
            )
            spec = figure_generation.check_reply(reply, full, kind=figure.kind, system=system_prompt.system, nonce=fenced.nonce)
        except AIOffError:
            _fail(db, project, actor, "ai_off")
        except LLMConfigurationError:
            _fail(db, project, actor, "llm_not_configured")
        except LLMResponseError:
            _fail(db, project, actor, "llm_response_invalid")
        except FigureGenerationError as exc:
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
            input_chars=len(fenced.block),
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
