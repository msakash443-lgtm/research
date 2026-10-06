# Codebase Analysis — research-main

This was a read-only analysis request, already completed in-conversation. No code changes are required or proposed. This file records the findings for reference; there is nothing to implement.

## Summary of findings

See the full analysis delivered in chat: folder structure, layer breakdown (frontend/API/backend/database), entry points, one traced workflow (submit research question → API → worker/inline executor → LLM → DB → polling UI), top-5 files, and plain-language architecture explanation.

Key facts (all verified by reading the files this session):
- Real app root: `research-main/research/backend/app/`. The outer `research-main/` and inner `research/` levels contain duplicate `docker-compose.yml`/`Dockerfile`/`*.db` artifacts — likely redundant nesting, not functional.
- Single FastAPI service (`app/main.py`) serves both the API (`/api/*`) and the static vanilla-JS frontend (`/`, `/assets`) from `app/web/`.
- Data layer: SQLAlchemy ORM (`app/models.py`), SQLite in dev / Postgres in prod (`docker-compose.yml`), Alembic migrations in `backend/migrations/`.
- Core domain logic: `app/agent/executor.py` builds evidence-bounded LLM prompts from saved project context/sources and persists an auditable `input_snapshot` with each `ResearchRun`.
- Background processing: `app/worker.py` polls `research_runs` for `queued` rows (`SKIP LOCKED`) in production; `run_research_inline` setting runs synchronously in dev.
- Auth: Google OAuth (`app/routers/auth.py`) plus a dev-only login bypass, both gated by `environment` setting.

## No action items

Nothing to approve or execute — this plan file exists only to satisfy the plan-mode approval gate for a task that was pure investigation.
