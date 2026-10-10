"""Task handlers. Importing this module registers them."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select

from app import audit
from app.agent.executor import execute_research_run
from app.database import SessionLocal
from app.connectors.base import ConnectorError
from app.connectors.factory import build_connector, build_unpaywall, enabled_lookup_names, unpaywall_unavailable_reason
from app.models import GateCode, Project, ResearchRun, ResearchRunStatus, SearchAlert, SearchQuery, SnowballRun, Source, utcnow
from app.agent.llm import LLMConfigurationError, OpenAICompatibleLLM
from app.llm_usage import ProjectMeter
from app.config import get_settings
from app.prescreen import PrescreenError, run_prescreen
from app.agent.embeddings import OpenAICompatibleEmbeddings
from app.models import ClusterRun
from app.thematic_clusters import ClusteringError, run_clustering
from app.oa_fetch import OaFetchError, fetch_open_access
from app.object_storage import get_object_store
from app.safe_fetch import FetchRefused, fetch_pdf
from app.search_query import BooleanQuery, QueryError
from app.source_verification import verify_source
from app.search_runner import SearchError, SearchFailed, rerun_search, run_search
from app.screening import screening_locked
from app.snowball import SnowballError, SnowballFailed, run_snowball
from app.query_alerts import QUERY_ALERT, _aware, latest_search, record_alert_run
from app.zotero import ZoteroError, build_zotero_client, sync_pull, sync_push
from app.task_registry import ClaimedTask, PermanentTaskError, TaskOwnershipLost, register

logger = logging.getLogger(__name__)

RESEARCH_RUN = "research_run"
SEARCH_RUN = "search_run"
SOURCE_CHECK = "source_check"
SCREENING_PRESCREEN = "screening_prescreen"
THEMATIC_CLUSTERING = "thematic_clustering"
FULLTEXT_FETCH = "fulltext_fetch"
SNOWBALL_RUN = "snowball_run"


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
                db, project, llm=OpenAICompatibleLLM(
                    settings,
                    meter=ProjectMeter(project.id, settings, purpose="prescreen", budget_override=project.token_budget_override),
                ), min_confidence=settings.prescreen_min_confidence,
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
                db, project=project, source=source, unpaywall=build_unpaywall(),
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
