"""Durable database-backed worker for research runs.

Run this as a separate process in Docker/production. It claims queued rows and
does not execute arbitrary user code or visit user-supplied URLs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.agent.executor import execute_research_run
from app.config import get_settings
from app.database import SessionLocal
from app.models import ResearchRun, ResearchRunStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def claim_next_run() -> str | None:
    db = SessionLocal()
    try:
        statement = (
            select(ResearchRun)
            .where(ResearchRun.status == ResearchRunStatus.queued)
            .order_by(ResearchRun.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        run = db.scalar(statement)
        if run is None:
            return None
        run.status = ResearchRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        db.commit()
        return str(run.id)
    except Exception:
        db.rollback()
        logger.exception("Unable to claim a research run")
        return None
    finally:
        db.close()


def run_forever() -> None:
    settings = get_settings()
    logger.info("Research worker started")
    while True:
        run_id = claim_next_run()
        if run_id is None:
            time.sleep(settings.research_worker_poll_seconds)
            continue
        try:
            execute_research_run(run_id)
        except Exception:
            logger.exception("Research run %s failed", run_id)


if __name__ == "__main__":
    run_forever()
