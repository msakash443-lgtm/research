"""Task handler for licence-checked arXiv PDF retrieval."""

from __future__ import annotations

import uuid

from app import audit
from app.config import get_settings
from app.connectors.access import is_enabled
from app.connectors.arxiv import ArxivConnector
from app.database import SessionLocal
from app.models import Project, Source
from app.object_storage import get_object_store
from app.oa_fetch import OaFetchError
from app.safe_fetch import FetchRefused, fetch_pdf
from app.task_registry import ClaimedTask, PermanentTaskError, register
from app.arxiv_fulltext import ARXIV_FULLTEXT_FETCH, fetch_arxiv_fulltext


@register(ARXIV_FULLTEXT_FETCH)
def handle_arxiv_fulltext_fetch(task: ClaimedTask) -> None:
    payload = task.payload or {}
    try:
        project_id = uuid.UUID(str(payload["project_id"]))
        source_id = uuid.UUID(str(payload["source_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("An arxiv_fulltext_fetch task needs project_id and source_id.") from exc

    settings = get_settings()
    if not is_enabled("arxiv", settings.connectors_enabled, settings.connectors_allow_scraping):
        raise PermanentTaskError("The arXiv connector is not enabled on this server")

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        source = db.get(Source, source_id)
        if project is None or source is None or source.project_id != project_id or source.merged_into is not None:
            raise PermanentTaskError("The source for this arXiv fetch no longer exists in the project.")
        try:
            fetch_arxiv_fulltext(
                db,
                project=project,
                source=source,
                arxiv=ArxivConnector(),
                fetch_pdf=lambda url: fetch_pdf(
                    url,
                    max_bytes=settings.object_storage_max_bytes,
                    timeout_seconds=settings.fulltext_fetch_timeout_seconds,
                ),
                store=get_object_store(settings),
                allowed_licences=settings.fulltext_store_licences,
                requested_by=str(payload.get("requested_by") or audit.SYSTEM_WORKER),
                before_write=task.ensure_owned,
            )
        except (OaFetchError, FetchRefused) as exc:
            db.rollback()
            raise PermanentTaskError(str(exc)) from exc
        task.ensure_owned(db)
        db.commit()
    finally:
        db.close()
