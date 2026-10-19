"""Task handlers. Importing this module registers them."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select

from app import audit, own_paper
from app.agent.executor import execute_research_run
from app.ai_extraction import AiExtractionError, run_ai_extraction
from app import extraction_suggestions  # noqa: F401  (registers the extraction suggestion handler)
from app.database import SessionLocal
from app.connectors.base import ConnectorError
from app.connectors.factory import (
    build_core_oa, build_openalex_oa, build_unpaywall, enabled_lookup_names, oa_source_enabled, unpaywall_unavailable_reason,
)
from app.connector_breaker_store import build_connector
from app.models import GateCode, OwnPaperLookup, Project, ResearchRun, ResearchRunStatus, SearchAlert, SearchQuery, SnowballRun, Source, utcnow
from app.agent.llm import LLMConfigurationError, LLMResponseError, OpenAICompatibleLLM
from app.ai_controls import AIOffError, ensure_embeddings_allowed, llm_for_project
from app.llm_usage import ProjectMeter
from app.config import get_settings
from app.own_paper_lookup import record_to_source_fields, resolve_by_doi, resolve_by_title
from app.prescreen import PrescreenError, run_prescreen
from app.agent.embeddings import OpenAICompatibleEmbeddings
from app.models import ClusterRun, SeedCheckRun
from app.seed_check_runner import run_seed_check
from app.thematic_clusters import ClusteringError, run_clustering
from app.oa_fetch import OaFetchError, OaSource, fetch_open_access
from app.object_storage import ObjectStorageError, get_object_store
from app.safe_fetch import FetchRefused, fetch_pdf
from app.search_query import BooleanQuery, QueryError
from app.source_verification import verify_source
from app.search_runner import SearchError, SearchFailed, rerun_search, run_search
from app.schemas import SourceCreate
from app.screening import screening_locked
from app.snowball import SnowballError, SnowballFailed, run_snowball
from app.query_alerts import QUERY_ALERT, _aware, latest_search, record_alert_run
from app.zotero import ZoteroError, build_zotero_client, sync_pull, sync_push
from app.task_registry import ClaimedTask, PermanentTaskError, TaskOwnershipLost, register
from app.similarity_task import run_similarity_check
from app.section_draft_task import run_section_draft, scrub_draft_payload
from app.workspace_ai_task import run_workspace_ai_action, scrub_action_payload

logger = logging.getLogger(__name__)

RESEARCH_RUN = "research_run"
SEARCH_RUN = "search_run"
SOURCE_CHECK = "source_check"
SCREENING_PRESCREEN = "screening_prescreen"
THEMATIC_CLUSTERING = "thematic_clustering"
WORKSPACE_AI_ACTION = "workspace_ai_action"
FULLTEXT_FETCH = "fulltext_fetch"
AI_EXTRACTION = "ai_extraction"
SNOWBALL_RUN = "snowball_run"
SEED_FALSE_EXCLUSION_CHECK = "seed_false_exclusion_check"
SIMILARITY_CHECK = "similarity_check"
OWN_PAPER_LOOKUP = "own_paper_lookup"
SECTION_DRAFT = "section_draft"


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
        execute_research_run(run_id, ensure_owned=task.ensure_owned)
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
                db, project, llm=llm_for_project(settings, project, purpose="prescreen"),
                min_confidence=settings.prescreen_min_confidence,
                actor=str(payload.get("actor") or audit.SYSTEM_WORKER), ensure_owned=task.ensure_owned,
                autoexclude_default_threshold=settings.screening_ai_autoexclude_confidence_threshold,
                autoexclude_sample_rate=settings.screening_ai_autoexclude_sample_rate,
                agreement_min_n=settings.screening_ai_agreement_min_n,
                agreement_min_kappa=settings.screening_ai_agreement_min_kappa,
            )
        except (AIOffError, LLMConfigurationError, PrescreenError) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
        if result.failed:
            raise PermanentTaskError(
                f"{result.failed} record(s) could not be pre-screened ({result.suggested} were); they are left for a person. See the audit log."
            )
    finally:
        db.close()


@register(SEED_FALSE_EXCLUSION_CHECK, requires_gate=GateCode.G2)
def handle_seed_false_exclusion_check(task: ClaimedTask) -> None:
    """Ask the pre-screen about each seed paper that matches a stored source and store its answers (M2.9.1).

    Needs G2 like pre-screening (the criteria it screens against are the approved ones). Writes no screening
    decision. An answer that can't be used is kept on its seed; the run is stored and the task then fails, so
    the gap is visible instead of the rate looking complete.
    """
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        run_id = uuid.UUID(str(payload["run_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A seed_false_exclusion_check task needs a project_id and a run_id.") from exc
    settings = get_settings()
    db = SessionLocal()
    try:
        if db.get(SeedCheckRun, run_id) is not None:
            return  # an earlier attempt stored this run before the worker could report it
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this seed check no longer exists.")
        try:
            run = run_seed_check(
                db, project, llm=llm_for_project(settings, project, purpose="seed_check"),
                min_confidence=settings.prescreen_min_confidence,
                autoexclude_default_threshold=settings.screening_ai_autoexclude_confidence_threshold,
                actor=str(payload.get("actor") or audit.SYSTEM_WORKER), run_id=run_id, ensure_owned=task.ensure_owned,
            )
        except (AIOffError, LLMConfigurationError, PrescreenError) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
        if run.summary["failed"]:
            raise PermanentTaskError(
                f"The AI step failed on {run.summary['failed']} seed paper(s); the check is stored as incomplete."
            )
    finally:
        db.close()


@register(THEMATIC_CLUSTERING)
def handle_thematic_clustering(task: ClaimedTask) -> None:
    """Embed the project's sources and group them (M3.8.1). No gate: it decides nothing, it only groups for a person."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        run_id = uuid.UUID(str(payload["run_id"]))
        k, seed = int(payload["k"]), int(payload["seed"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A thematic_clustering task needs project_id, run_id, k and seed.") from exc
    settings = get_settings()
    db = SessionLocal()
    try:
        if db.get(ClusterRun, run_id) is not None:
            return  # an earlier attempt stored this run before the worker could report it
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this clustering no longer exists.")
        try:
            ensure_embeddings_allowed(project)
        except AIOffError as exc:
            raise PermanentTaskError(str(exc)) from exc
        embedder = OpenAICompatibleEmbeddings(
            settings,
            meter=ProjectMeter(project.id, settings, purpose="clustering", budget_override=project.token_budget_override),
        )
        try:
            run_clustering(
                db, project, embedder=embedder, k=k, seed=seed, actor=str(payload.get("actor") or audit.SYSTEM_WORKER),
                batch_size=settings.embedding_batch_size, max_sources=settings.cluster_max_sources, run_id=run_id,
                ensure_owned=task.ensure_owned,
            )
        except (LLMConfigurationError, ClusteringError) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
        db.commit()
    finally:
        db.close()


@register(FULLTEXT_FETCH)
def handle_fulltext_fetch(task: ClaimedTask) -> None:
    """Fetch one source's open-access PDF where its licence permits (M2.7.1). No gate: it decides nothing."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        source_id = uuid.UUID(str(payload["source_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A fulltext_fetch task needs project_id and source_id.") from exc
    reason = unpaywall_unavailable_reason()
    if reason:
        raise PermanentTaskError(reason)
    settings = get_settings()
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        source = db.get(Source, source_id)
        if project is None or source is None or source.project_id != project_id or source.merged_into is not None:
            raise PermanentTaskError("The source for this fetch no longer exists in the project.")
        try:
            fetch_open_access(
                db, project=project, source=source,
                sources=[
                    OaSource("unpaywall", build_unpaywall()),
                    OaSource("openalex", build_openalex_oa() if oa_source_enabled("openalex") else None),
                    OaSource("core", build_core_oa() if oa_source_enabled("core") else None),
                ],
                fetch_pdf=lambda url: fetch_pdf(url, max_bytes=settings.object_storage_max_bytes, timeout_seconds=settings.fulltext_fetch_timeout_seconds),
                store=get_object_store(settings), allowed_licences=settings.fulltext_store_licences,
                requested_by=str(payload.get("requested_by") or audit.SYSTEM_WORKER), before_write=task.ensure_owned,
            )
        except (OaFetchError, FetchRefused) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
        task.ensure_owned(db)
        db.commit()
    finally:
        db.close()


@register(AI_EXTRACTION)
def handle_ai_extraction(task: ClaimedTask) -> None:
    """Extract one source from its stored PDF; results stay unverified until a person reviews them."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        source_id = uuid.UUID(str(payload["source_id"]))
        schema_name = str(payload["schema_name"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("An ai_extraction task needs project_id, source_id and schema_name.") from exc
    settings = get_settings()
    db = SessionLocal()
    try:
        task.ensure_owned(db)
        project = db.get(Project, project_id)
        source = db.scalar(select(Source).where(Source.id == source_id, Source.project_id == project_id))
        if project is None or source is None:
            raise PermanentTaskError("The source for this extraction no longer exists in the project.")
        try:
            run_ai_extraction(
                db, project, source, schema_name=schema_name, task_id=task.id,
                actor=str(payload.get("requested_by") or audit.SYSTEM_WORKER), store=get_object_store(settings),
                llm=OpenAICompatibleLLM(
                    settings,
                    meter=ProjectMeter(project.id, settings, purpose="extraction", budget_override=project.token_budget_override),
                ),
                max_pages=settings.fulltext_max_pages, ensure_owned=task.ensure_owned,
            )
        except (AiExtractionError, LLMConfigurationError, LLMResponseError, ObjectStorageError) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
    finally:
        db.close()


@register(SNOWBALL_RUN, requires_gate=GateCode.G2)
def handle_snowball_run(task: ClaimedTask) -> None:
    """Chase citations from the start papers (M1.9). Bulk retrieval, so the task type needs gate G2."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        run_id = uuid.UUID(str(payload["run_id"]))
        connector_name = str(payload["connector"])
        directions = [str(d) for d in payload["directions"]]
        rounds, max_per_paper, max_new = int(payload["rounds"]), int(payload["max_per_paper"]), int(payload["max_new"])
        source_ids = [uuid.UUID(str(s)) for s in payload.get("source_ids") or []]
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError(
            "A snowball_run task needs project_id, run_id, connector, directions, rounds, max_per_paper and max_new."
        ) from exc
    db = SessionLocal()
    try:
        if db.get(SnowballRun, run_id) is not None:
            return  # an earlier attempt saved this run before the worker could report it; never run it twice
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this snowball run no longer exists.")
        if screening_locked(db, project):
            raise PermanentTaskError("Gate G3 is approved, so no new records can enter screening. Reopen the screening stage first.")
        try:
            run_snowball(
                db, project=project, actor=str(payload.get("actor") or audit.SYSTEM_WORKER), connector=build_connector(connector_name),
                run_id=run_id, directions=directions, rounds=rounds, max_per_paper=max_per_paper, max_new=max_new,
                include_seeds=bool(payload.get("include_seeds")), source_ids=source_ids, before_write=task.ensure_owned,
            )
        except SnowballFailed:
            db.commit()  # keep the snowball.failed audit event, then let the queue retry with backoff
            raise
        except (SnowballError, ConnectorError) as exc:
            db.rollback()
            raise PermanentTaskError(f"The snowball run could not be done: {exc}") from exc
        task.ensure_owned(db)
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ Zotero sync (M1.11.2)

ZOTERO_PULL = "zotero_pull"
ZOTERO_PUSH = "zotero_push"


@register(ZOTERO_PULL, requires_gate=GateCode.G2)
def handle_zotero_pull(task: ClaimedTask) -> None:
    """Pull items from the configured Zotero library into the project (M1.11.2.2). Bulk retrieval,
    so it needs G2, like database searches. Items arrive unverified; a retried run is a no-op."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A zotero_pull task needs a project_id.") from exc
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this Zotero pull no longer exists.")
        try:
            result = sync_pull(db, project, build_zotero_client(), collection_key=get_settings().zotero_collection_key,
                                before_write=task.ensure_owned)
        except (ZoteroError, ConnectorError) as exc:
            db.rollback()
            raise PermanentTaskError(f"The Zotero pull could not be done: {exc}") from exc
        task.ensure_owned(db)
        db.commit()
        logger.info("Zotero pull for project %s: %s", project_id, result)
    finally:
        db.close()


@register(ZOTERO_PUSH)
def handle_zotero_push(task: ClaimedTask) -> None:
    """Push the project's verified sources into the configured Zotero library (M1.11.2.3). Sends
    nothing unverified, creates nothing for a source that already has an item, and never updates
    or deletes an existing Zotero item; a re-run adopts instead of duplicating."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A zotero_push task needs a project_id.") from exc
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this Zotero push no longer exists.")
        try:
            result = sync_push(db, project, build_zotero_client(), collection_key=get_settings().zotero_collection_key,
                                before_write=task.ensure_owned)
        except (ZoteroError, ConnectorError) as exc:
            db.rollback()
            raise PermanentTaskError(f"The Zotero push could not be done: {exc}") from exc
        task.ensure_owned(db)
        db.commit()
        logger.info("Zotero push for project %s: %s", project_id, result)
    finally:
        db.close()


@register(QUERY_ALERT, requires_gate=GateCode.G2)
def handle_query_alert(task: ClaimedTask) -> None:
    """Re-run a saved search on its alert schedule (M1.12): surface papers new since the earlier
    versions. Bulk retrieval, so it needs G2, like a queued search run; a retried run is a no-op."""
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        alert_id = uuid.UUID(str(payload["alert_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("A query_alert task needs project_id and alert_id.") from exc
    enqueued_at = None
    if payload.get("enqueued_at"):
        try:
            enqueued_at = datetime.fromisoformat(str(payload["enqueued_at"]))
        except ValueError:
            enqueued_at = None
    actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this alert no longer exists.")
        alert = db.get(SearchAlert, alert_id)
        if alert is None:
            raise PermanentTaskError("The alert this task belongs to no longer exists.")
        if not alert.enabled:
            return  # disabled between enqueue and claim; the schedule simply stops
        latest = latest_search(db, project, alert.search_id)
        if latest is None:
            raise PermanentTaskError("The search this alert watches no longer exists.")
        if enqueued_at is not None and _aware(alert.last_run_at) is not None \
                and _aware(alert.last_run_at) >= _aware(enqueued_at):
            return  # a run already completed at or after this task was enqueued: a retried attempt
        try:
            connector = build_connector(latest.database)
            row = rerun_search(db, project=project, actor=actor, connector=connector, search_id=alert.search_id)
            result = record_alert_run(db, project, alert, row, actor=actor)
        except SearchFailed:
            db.commit()  # keep the search.failed audit event, then let the queue retry with backoff
            raise
        except (AlertError, SearchError, QueryError, ConnectorError, ValueError) as exc:
            db.rollback()
            raise PermanentTaskError(f"The alert re-run could not be done: {exc}") from exc
        task.ensure_owned(db)
        db.commit()
        logger.info("Alert %s: search %s v%s, %s new", alert_id, alert.search_id, result.get("version"), result.get("new"))
    finally:
        db.close()


@register(WORKSPACE_AI_ACTION, scrub_payload=scrub_action_payload)
def handle_workspace_ai_action(task: ClaimedTask) -> None:
    """One side-panel AI action on a section or note (X.31.11). No gate: it only proposes; a person accepts."""
    run_workspace_ai_action(task.payload or {})


@register(SECTION_DRAFT, scrub_payload=scrub_draft_payload)
def handle_section_draft(task: ClaimedTask) -> None:
    """Draft one section's opening prose from its bound claims (X.46.8 / M6.1.a). No gate: it only proposes a
    suggestion; a person must accept it and press Insert before anything reaches the document (Q27)."""
    run_section_draft(task.payload or {})


@register(SIMILARITY_CHECK)
def handle_similarity_check(task: ClaimedTask) -> None:
    """One similarity check of a saved section version against the project's sources (X.31.14). No gate: nothing leaves the server."""
    run_similarity_check(task.payload or {})


def _own_paper_lookup_write_terminal(db, project_id: uuid.UUID, payload: dict, task_id: uuid.UUID, *, status: str, reason: str, detail: str) -> None:
    """Record a terminal own-paper lookup outcome that is not `included`: `not_found` (every enabled
    connector cleanly answered "no match") or `failed` (refused outright: locked, misconfigured, a
    duplicate once resolved, or bad matched metadata). Separate from `had_error` retries, which leave
    no row so the next attempt can still resolve it."""
    row = OwnPaperLookup(
        project_id=project_id, task_id=task_id, doi=payload.get("doi"), title=payload.get("title"),
        status=status, requested_by=str(payload.get("actor") or audit.SYSTEM_WORKER), detail=detail,
    )
    db.add(row)
    audit.record(
        db, actor=audit.SYSTEM_WORKER, action=f"source.own_paper_lookup_{status}", project_id=project_id,
        payload={"task_id": str(task_id), "reason": reason},
    )


@register(OWN_PAPER_LOOKUP)
def handle_own_paper_lookup(task: ClaimedTask) -> None:
    """Resolve a queued DOI-only/title-only own-paper lookup (M2.14.1) through the enabled lookup
    connectors, then include it the same way `POST .../sources/own` does (`app/own_paper.py`).

    No gate: adding one's own paper is never blocked by G2 (it is not a database search), only by G3
    being approved (checked here, since it may have been approved after this task was queued). Writes
    one `OwnPaperLookup` row with a terminal status; a transient connector failure raises instead, so
    the queue retries with backoff, and no row is written until there is a real outcome to report.
    """
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentTaskError("An own_paper_lookup task needs a project_id.") from exc
    doi, title = payload.get("doi"), payload.get("title")
    if bool(doi) == bool(title):
        raise PermanentTaskError("An own_paper_lookup task needs exactly one of doi or title.")

    db = SessionLocal()
    try:
        if db.scalar(select(OwnPaperLookup.id).where(OwnPaperLookup.task_id == task.id)) is not None:
            return  # an earlier attempt already wrote the outcome; never write it twice
        project = db.get(Project, project_id)
        if project is None:
            raise PermanentTaskError("The project for this lookup no longer exists.")
        if screening_locked(db, project):
            _own_paper_lookup_write_terminal(db, project.id, payload, task.id, status="failed", reason="screening_locked", detail=own_paper.LOCKED_MESSAGE)
            task.ensure_owned(db)
            db.commit()
            return
        names = enabled_lookup_names()
        if not names:
            _own_paper_lookup_write_terminal(
                db, project.id, payload, task.id, status="failed", reason="no_connector",
                detail="No lookup connector (crossref, semantic_scholar, openalex) is enabled on this server.",
            )
            task.ensure_owned(db)
            db.commit()
            raise PermanentTaskError("No lookup connector is enabled; the lookup cannot be retried until one is.")
        connectors = [build_connector(n) for n in names]
        result = resolve_by_doi(doi, connectors) if doi else resolve_by_title(title, connectors)
        if result.record is None:
            if result.had_error:
                raise ConnectorError("Every enabled lookup connector failed for this request; retrying.")
            _own_paper_lookup_write_terminal(
                db, project.id, payload, task.id, status="not_found", reason="not_found",
                detail="No enabled lookup connector has a matching record for this " + ("DOI." if doi else "title."),
            )
            task.ensure_owned(db)
            db.commit()
            return
        if result.record.doi and db.scalar(
            select(Source.id).where(Source.project_id == project.id, Source.doi == result.record.doi, Source.merged_into.is_(None))
        ):
            _own_paper_lookup_write_terminal(db, project.id, payload, task.id, status="failed", reason="duplicate_doi", detail="This DOI is already in the project.")
            task.ensure_owned(db)
            db.commit()
            return
        try:
            item = SourceCreate(**record_to_source_fields(result.record))
        except (ValidationError, ValueError) as exc:
            _own_paper_lookup_write_terminal(db, project.id, payload, task.id, status="failed", reason="bad_metadata", detail=f"The matched record's metadata was invalid: {exc}")
            task.ensure_owned(db)
            db.commit()
            return
        actor = str(payload.get("actor") or audit.SYSTEM_WORKER)
        source = own_paper.create_own_paper_source(
            db, project=project, actor=actor, item=item, note=f"added by researcher; resolved via {result.connector}",
        )
        row = OwnPaperLookup(
            project_id=project.id, task_id=task.id, doi=doi, title=title, status="included",
            source_id=source.id, matched_connector=result.connector, requested_by=actor,
        )
        db.add(row)
        audit.record(
            db, actor=actor, action="source.own_paper_lookup_resolved", project_id=project.id,
            payload={"task_id": str(task.id), "source_id": str(source.id), "connector": result.connector},
        )
        task.ensure_owned(db)
        db.commit()
    finally:
        db.close()
