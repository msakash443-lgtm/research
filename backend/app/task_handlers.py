"""Task handlers. Importing this module registers them."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app import audit
from app.agent.executor import execute_research_run
from app.database import SessionLocal
from app.connectors.base import ConnectorError
from app.connectors.factory import build_connector, enabled_lookup_names
from app.models import GateCode, Project, ResearchRun, ResearchRunStatus, SearchQuery, Source, utcnow
from app.agent.llm import LLMConfigurationError, OpenAICompatibleLLM
from app.config import get_settings
from app.prescreen import PrescreenError, run_prescreen
from app.search_query import BooleanQuery, QueryError
from app.source_verification import verify_source
from app.search_runner import SearchError, SearchFailed, rerun_search, run_search
from app.task_registry import ClaimedTask, PermanentTaskError, TaskOwnershipLost, register

logger = logging.getLogger(__name__)

RESEARCH_RUN = "research_run"
SEARCH_RUN = "search_run"
SOURCE_CHECK = "source_check"
SCREENING_PRESCREEN = "screening_prescreen"


def _research_run_abandoned(task: ClaimedTask) -> None:
    """The queue gave up on this run. Make sure the run itself doesn't keep looking in progress."""
    run_id = (task.payload or {}).get("run_id")
    db = SessionLocal()
    try:
        run = db.get(ResearchRun, uuid.UUID(str(run_id))) if run_id else None
        if run is None or run.status not in {ResearchRunStatus.queued, ResearchRunStatus.running}:
            return  # already finished or failed with its own explanation
        run.status = ResearchRunStatus.failed
        run.error_message = (
            f"The run stopped responding and was abandoned after {task.attempt} attempts. "
            "Check the worker logs, then submit the question again."
        )
        run.completed_at = utcnow()
        audit.record(
            db, actor=audit.SYSTEM_WORKER, action="research_run.abandoned", project_id=run.project_id,
            payload={"run_id": str(run.id), "attempts": task.attempt},
        )
        db.commit()
    finally:
        db.close()


@register(RESEARCH_RUN, on_failure=_research_run_abandoned)
def handle_research_run(task: ClaimedTask) -> None:
    run_id = (task.payload or {}).get("run_id")
    if not run_id:
        raise PermanentTaskError("A research_run task needs a run_id in its payload.")
    try:
        execute_research_run(run_id)  # M0.6.9: pass ensure_owned=task.ensure_owned once the executor accepts it
    except TaskOwnershipLost:
        raise  # another worker has the run; nothing was written by this attempt
    except Exception as exc:
        # The executor has already marked the run failed and recorded why; retrying can't help.
        logger.exception("Research run %s failed", run_id)
        raise PermanentTaskError("The research run failed unexpectedly; see the run record.") from exc


@register(SEARCH_RUN, requires_gate=GateCode.G2)
def handle_search_run(task: ClaimedTask) -> None:
    """Run a queued database search. The task type needs gate G2, so it only gets here once a person approved it."""
    payload = task.payload or {}
    try:
        search_id = uuid.UUID(str(payload["search_id"]))
        project_id = uuid.UUID(str(payload["project_id"]))
        version = int(payload["version"])
        database = str(payload["database"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A search_run task needs project_id, search_id, version and database.") from exc
    actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    max_results = int(payload.get("max_results") or 500)

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this search no longer exists.")
        done = db.scalar(select(SearchQuery.id).where(SearchQuery.search_id == search_id, SearchQuery.version == version))
        if done is not None:
            return  # an earlier attempt saved this run before the worker could report it; never run it twice
        try:
            connector = build_connector(database)
            if payload.get("rerun"):
                row = rerun_search(db, project=project, actor=actor, connector=connector, search_id=search_id,
                                   max_results=max_results, version=version)
            else:
                query = BooleanQuery.model_validate({"blocks": payload.get("blocks") or []})
                row = run_search(db, project=project, actor=actor, connector=connector, query=query,
                                 filters=payload.get("filters") or {}, max_results=max_results, search_id=search_id)
        except SearchFailed:
            db.commit()  # keep the search.failed audit event, then let the queue retry with backoff
            raise
        except (SearchError, QueryError, ConnectorError, ValueError) as exc:
            db.rollback()
            raise PermanentTaskError(f"The search could not be run: {exc}") from exc
        task.ensure_owned(db)
        db.commit()
        logger.info("Search %s v%s saved with %s results", search_id, row.version, row.n_results)
    finally:
        db.close()


@register(SOURCE_CHECK)
def handle_source_check(task: ClaimedTask) -> None:
    """Check one source's metadata against scholarly services (no gate: it only records evidence about metadata)."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        source_id = uuid.UUID(str(payload["source_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A source_check task needs project_id and source_id.") from exc
    names = enabled_lookup_names()
    if not names:
        raise PermanentTaskError("No lookup connector (crossref, semantic_scholar, openalex) is enabled.")
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        source = db.get(Source, source_id)
        if project is None or source is None or source.project_id != project_id or source.merged_into is not None:
            raise PermanentTaskError("The source for this check no longer exists in the project.")
        verify_source(
            db, project=project, source=source, connectors=[build_connector(n) for n in names],
            requested_by=str(payload.get("requested_by") or audit.SYSTEM_WORKER),
        )
        task.ensure_owned(db)
        db.commit()
    finally:
        db.close()


@register(SCREENING_PRESCREEN, requires_gate=GateCode.G2)
def handle_screening_prescreen(task: ClaimedTask) -> None:
    """Ask the model for a suggestion on each unscreened source. Needs G2: the criteria it screens against are the approved ones."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A screening_prescreen task needs a project_id.") from exc
    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this pre-screen no longer exists.")
        try:
            result = run_prescreen(
                db, project, llm=OpenAICompatibleLLM(settings), min_confidence=settings.prescreen_min_confidence,
                actor=str(payload.get("actor") or audit.SYSTEM_WORKER), ensure_owned=task.ensure_owned,
            )
        except (LLMConfigurationError, PrescreenError) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
        if result.failed:
            raise PermanentTaskError(
                f"{result.failed} record(s) could not be pre-screened ({result.suggested} were); they are left for a person. See the audit log."
            )
    finally:
        db.close()
