"""The `figure_accuracy_check` task body (plan M8.6): the AI labels each element of one figure version against the paper.

Mirrors `figure_generate_task.py`. A refused or failed check writes an `ai.failed` audit event with a stable code and
stores nothing. A labelled reply is stored as a `checked` `FigureCheck`; an abstention as `insufficient_evidence` with
its reason. Both record the model and the prompt. Nothing here changes the figure.
"""

from __future__ import annotations

import uuid

from app import audit, figure_accuracy, figure_generation, figure_store
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.ai_controls import AIOffError, llm_for_project
from app.config import get_settings
from app.database import SessionLocal
from app.figure_accuracy import AccuracyError
from app.fulltext import FullTextError
from app.models import Figure, FigureCheck, Project, UploadedDocument
from app.prompt_registry import load_prompt
from app.task_registry import ClaimedTask, PermanentTaskError
from app.uploaded_documents import error_code
from app.workspace_ai_task import _record_failure

# Module-level (name, version) constant: the evaluation rule (evals/baselines.py) finds prompts by this.
FIGURE_ACCURACY_PROMPT = figure_accuracy.PROMPT

PRODUCER = "figure_accuracy_check"
ACTION = "check"
PURPOSE = "figure_accuracy_check"


def scrub_figure_check_payload(payload: dict) -> dict:
    """The payload kept once the task has ended: ids and the actor only, so nothing is removed."""
    return dict(payload)


def _fail(db, project: Project, actor: str, code: str) -> None:
    _record_failure(db, project, actor, ACTION, code)
    raise PermanentTaskError(f"figure accuracy check stopped: {code}")


def run_figure_accuracy_check(payload: dict, task: ClaimedTask) -> None:
    """The body of the `figure_accuracy_check` task. Raises `PermanentTaskError` on any refusal or failure."""
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        figure_id = uuid.UUID(str(payload["figure_id"]))
        number = int(payload["number"])
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A figure_accuracy_check task needs project_id, figure_id and number.") from exc

    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        figure = db.get(Figure, figure_id)
        if project is None or figure is None or figure.project_id != project.id:
            raise PermanentTaskError("The figure for this check no longer exists.")
        version = figure_store.version_by_number(db, figure, number)
        if version is None:
            raise PermanentTaskError("The figure version for this check no longer exists.")
        document = db.get(UploadedDocument, figure.uploaded_document_id)
        if document is None or document.withdrawn_at is not None:
            _fail(db, project, actor, "document_withdrawn")
        try:
            full = figure_store.document_full_text(settings, document)
        except FullTextError as exc:
            _fail(db, project, actor, error_code(exc))

        system_prompt = load_prompt(*FIGURE_ACCURACY_PROMPT)
        try:
            items = figure_accuracy.elements(version.spec_json)
            fenced = figure_generation.fence(figure_generation.passages(full, figure.kind))
            llm = llm_for_project(settings, project, purpose=PURPOSE)
            reply = llm.complete_json(
                system_prompt.system,
                system_prompt.render_user(document=fenced.block, elements=figure_accuracy.render(items)),
                figure_accuracy.schema(items),
            )
            verdict = figure_accuracy.check_reply(reply, items, system=system_prompt.system, nonce=fenced.nonce)
        except AIOffError:
            _fail(db, project, actor, "ai_off")
        except LLMConfigurationError:
            _fail(db, project, actor, "llm_not_configured")
        except LLMResponseError:
            _fail(db, project, actor, "llm_response_invalid")
        except AccuracyError as exc:
            _fail(db, project, actor, exc.code)

        task.ensure_owned(db)
        check = FigureCheck(
            project_id=project.id,
            figure_version_id=version.id,
            status=verdict.status,
            reason=verdict.reason or None,
            labels_json=verdict.labels,
            model_id=llm._model,
            prompt_version=system_prompt.ref,
            created_by=actor,
        )
        db.add(check)
        db.flush()
        audit.record(
            db, actor=actor, action="figure.checked", project_id=project.id,
            payload={
                "figure_id": str(figure.id), "number": version.number, "check_id": str(check.id),
                "status": verdict.status, "labelled": len(verdict.labels),
            },
            model_id=llm._model, prompt_version=system_prompt.ref,
        )
        db.commit()
    finally:
        db.close()
