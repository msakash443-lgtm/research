# Repository Guidelines

> **Read first, any tool or person:** before editing code, read `plan.md` §1 and §1.8, claim one task by hand-editing its line (`[~]`, `owner: @you`, `since: <date>`) and saving `plan.md`, then work. Append one Work Log line at the end. Commits are manual, made by the developer: leave changes in the working tree with a suggested message; never commit/push/stash/reset unless the user asks. This file is the canonical copy; `CLAUDE.md` repeats it for Claude Code.

## Assistant Checklist (every AI coding assistant, every session)

**Do**
- **Read the code first.** Before editing, read the files you will change, their tests, and the `plan.md` task line.
- **Show the plan before making changes.** State which files you will touch and what will change, then edit.
- **Ask before deleting anything:** files, code, tests, data, migrations or branches, including untracked files (`plan.md` rule 44).
- **Make one change at a time.** Work on one task and one logical change, and verify it before starting the next (rule 36).
- **Write a test** for every behaviour change or bug fix. It must fail without the change. For docs-only changes, say that no test applies.
- **Run the test**, then the full `pytest -q` from the repo root. Report the real result, including failures and their output (rule 37).
- **Say when uncertain.** Mark unverified claims as unverified, and ask rather than guess (rule 35).
- **Clearly say what changed:** files changed, behaviour changed, tests run with their results, and a suggested commit message (rules 42, 45).

**Don't**
- **Touch `.env` or other secret files.** Don't read, edit, print or copy them. Document new settings in `.env.example` with placeholder values.
- **Add new packages unnecessarily.** Use the standard library or existing dependencies first. Ask before adding anything to `backend/requirements.txt`.
- **Fake tests or claim tests passed when they didn't.** No test that can't fail, and no `skip`/`xfail` just to make the suite pass. Never report a run that didn't happen.
- **Leave TODOs "for later."** No `TODO`/`FIXME`, stubs or placeholders in the diff. Discovered work goes into `plan.md` as a new task (rule 8).
- **Rewrite working code unnecessarily.** Make the smallest change that meets the task's acceptance check (rule 36).
- **Put secrets in code.** Keys, tokens and passwords never go in code, tests, docs, prompts, fixtures or logs. Read them through `Settings` (`backend/app/config.py`).

## Project Overview

A research-automation platform for literature review / evidence synthesis. Per `docs/research-automation-spec.md`, the goal is to "automate the repeatable, high-volume work of research (searching, screening, extracting, organizing, formatting, tracking) so the scholar can spend time on judgment-heavy work." Core design stance: the system is modeled on how a research scholar thinks — every module maps to a scholarly decision, produces an auditable artifact, and **the human stays the accountable author** (AI proposes, human decides; every consequential step has an approval gate).

The currently implemented app (repo root, `research-main/`) is a narrower "safe V1 evidence-synthesis agent" — a project workspace for a question, decisions, sources, excerpts and agent runs, with no arbitrary web scraping, code execution, or file uploads yet. Full scope is tracked milestone-by-milestone in `plan.md` against `docs/research-automation-spec.md`.

## Repo / Workspace Layout

Two repos live side by side; **never cross the boundary by importing code**:

| Path | What |
|---|---|
| `research-main/` (repo root) | This app — FastAPI backend (`backend/app/`), Alembic migrations, static web UI, tests. **This is where you work.** |
| `../AutoResearchClaw-main/AutoResearchClaw-main/` | **ARC** — a separate retrieval microservice (web search / crawl / PDF extraction only), entry point `researchclaw/server/retrieval_api.py`. |

**ARC is called over HTTP only** (`POST /v1/retrieve`, bearer token `ARC_RETRIEVAL_TOKEN`) — its Python package is **never imported** into research-main (it fails open and auto-approves gates, which this app must never do). Two separate venvs enforce this at the dependency level (see Runtime/Tooling below). ARC-retrieved sources are stored `metadata_verified=false`, labelled "Auto-retrieved, unverified," and their text is fenced as untrusted before it reaches any prompt.

Workspace root also holds: `plan.md` (the living task plan — read before any change), `docs/research-automation-spec.md` (scope source of truth), `docs/traceability.md`, `milestone-reports/`, `docs/ARC_INTEGRATION PLAN.md`.

## Architecture & Data Flow

**Entry point**: `backend/app/main.py` builds the FastAPI app, mounts 14 routers under `/api` (`auth, projects, audit, gates, stage, profile, connectors, criteria, prisma, searches, seeds, members, sources, research_runs`), plus `/health`, `/ready`, and the static UI at `/` and `/assets`. Middleware order: `ContentLengthLimitMiddleware` → `TrustedHostMiddleware` → session middleware → (optional) CORS → custom `security_headers`.

**Request flow**: router → FastAPI `Depends()` chain → SQLAlchemy `Session` → ORM models (`app/models.py`) → Pydantic `response_model`. Authorization is a dependency factory: `project_access(allowed_roles)` (`app/dependencies.py`) loads the `Project`, resolves the caller's `ProjectRole`, and 404s if they're not a member (hides existence) / 403s if their role isn't allowed. Role sets are named constants: `READ_ROLES`, `WRITE_ROLES`, `APPROVE_ROLES`, `MANAGE_ROLES` — **not a ladder** (a supervisor can approve but not edit; a co-author can edit but not approve).

**Background jobs — DB-backed task queue, not Celery/Redis**:
1. Routers call `task_queue.enqueue_task(...)`, creating a `Task` row. If the task type `requires_gate` and that gate isn't approved, it's inserted `blocked` instead of `queued`.
2. `app/worker.py::run_forever()` is a **separate process** (Docker: `python -m app.worker`) that loops: `task_runner.run_one_task()` → `task_queue.reap_expired_tasks()` → `gates.release_tasks_for_approved_gates()` (heals approve/enqueue races), sleeping `RESEARCH_WORKER_POLL_SECONDS` between.
3. `task_queue.claim_next_task()` atomically claims the oldest due task with a lease (`TASK_LEASE_SECONDS`).
4. Handlers are registered via `@register(task_type, on_failure=..., requires_gate=...)` in `app/task_handlers.py`; they must call `task.ensure_owned(db)` before side effects — if the lease was lost to another worker, raises `TaskOwnershipLost` and the handler stops writing.
5. Failure: any exception retries with exponential backoff (`base * 2^(n-1)`, capped at `TASK_RETRY_MAX_SECONDS`) up to the type's max attempts; `task_registry.PermanentTaskError` fails immediately, no retry. On final failure, `FAILURE_HOOKS` mark the related domain record (e.g. `ResearchRun`) failed — **never left looking in-progress, never a placeholder result**.

**Agent / LLM execution**: `app/agent/executor.py::execute_research_run(run_id)` loads the run + evidence (capped: `MAX_SOURCES=25`, `MAX_TOTAL_EVIDENCE_CHARS=24000`), optionally ingests ARC results (failure there never fails the run), builds a prompt via `app/prompt_registry.py` (version-pinned, e.g. `EVIDENCE_SYNTHESIS_PROMPT = ("evidence_synthesis", 2)` — changing wording means adding a new prompt file and moving the pin deliberately), calls the LLM via `app/agent/llm.py`, and persists results/`Artifact`s.

**Gates & stages**: `app/gates.py` defines 11 gate codes (`GateCode` G1–G11, spec §8) with per-gate approver role sets; `ensure_gates()` creates pending rows idempotently; `release_tasks_for_gate()` unblocks queued tasks on approval. `app/stage_machine.py` documents the 16-stage project workflow (IDEA → SCOPED(G1) → ... → ACCEPTED); `advance_stage()` is **not idempotent on its own** — callers must pass the explicit target stage so a retried request is rejected, not double-applied.

**Audit**: `app/audit.py::record()` just `db.add()`s an `AuditEvent` — it does not commit, so it rides the caller's transaction atomically. Payloads hold ids/counts/statuses only, never secrets, prompts, model answers, or source text. Append-only enforced at the DB layer by `app/audit_guard.py`.

## Key Directories

| Path | Purpose |
|---|---|
| `backend/app/main.py` | App factory, middleware, router mounts |
| `backend/app/config.py` | `Settings` (pydantic-settings, `.env`-backed), `get_settings()` |
| `backend/app/database.py` | Engine, `SessionLocal`, `Base`, `get_db()` |
| `backend/app/dependencies.py` | `current_user`, `project_access(roles)`, role-set constants |
| `backend/app/models.py` | All SQLAlchemy ORM models + enums |
| `backend/app/schemas.py` | Pydantic `*Create`/`*Read`/`*Base` request/response schemas |
| `backend/app/routers/` | One router per resource (`gates.py`, `sources.py`, `searches.py`, `members.py`, …) |
| `backend/app/task_queue.py`, `task_registry.py`, `task_runner.py`, `task_handlers.py` | Task queue core |
| `backend/app/agent/` | `executor.py` (run orchestration), `llm.py`, `arc_client.py` (HTTP-only ARC client), `context.py` |
| `backend/app/connectors/` | External scholarly APIs: `openalex.py`, `crossref.py`, `semantic_scholar.py`, `unpaywall.py`, `factory.py`, `access.py` (enable-gating) |
| `backend/app/prompts/` | Versioned prompt templates (`evidence_synthesis.v1.toml`, `.v2.toml`) |
| `backend/app/profiles/` | Discipline profiles (`economics.toml`, `management.toml`, `social_sciences.toml`, `psychology.toml`, `commerce.toml`, `english_literature.toml`) |
| `backend/app/{dedupe,merge,source_merge,citation_verifier,doi,search_query,search_query_adapters,search_runner,known_items,prisma}.py` | Pure, DB/network-free business-logic modules (each paired 1:1 with a test file) |
| `backend/app/{audit_guard,excerpt_guard,untrusted_text,answer_guard}.py` | Integrity guards: append-only audit, immutable excerpts, untrusted-text fencing, LLM-output validation |
| `backend/app/web/` | Static UI (`index.html`, `app.js`, `styles.css`), served at `/` and `/assets` |
| `backend/migrations/versions/` | Alembic migrations, `YYYYMMDD_NNNN_slug.py` |
| `backend/tests/` | Flat pytest suite, mirrors `app/` module names 1:1 |

## Development Commands

Run everything from the repo root `research-main/` (contains `pytest.ini`, `alembic.ini`, `docker-compose.yml`):

```powershell
# Docker (recommended)
Copy-Item .env.example .env
# edit POSTGRES_PASSWORD and SESSION_SECRET
docker compose up --build        # → http://localhost:8000

# Local, no Docker (set RUN_RESEARCH_INLINE=true in .env first)
python -m pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend

# Tests
pytest -q

# Migrations (production / release step)
alembic -c alembic.ini upgrade head

# Build a standalone image
docker build -t research-ai:latest .

# Worker (separate process)
python -m app.worker
```

No lint/format tooling is configured in the repo (no ruff/black/flake8 config found) — follow existing style by hand.

## Code Conventions & Common Patterns

- **Naming**: snake_case for modules/functions. Pydantic schemas: `<Entity>Create` / `<Entity>Read` / `<Entity>Base` (e.g. `SourceCreate`/`SourceRead`); some `Read` schemas subclass their `Create` counterpart.
- **Dependency injection**: FastAPI `Depends()` everywhere; `project_access(allowed_roles)` is a dependency-*factory* — call it with a role set to get the dependency, don't inline role checks in handlers.
- **Error handling**: raise `HTTPException` directly. Convention: **404** when a resource doesn't exist *or* the caller has no access (never leak existence); **403** only once membership/identity is confirmed but role is insufficient.
- **Audit everything that mutates state**: call `audit.record(db, actor=..., action=..., ...)` inside the same transaction as the change — never a separate commit.
- **Sync, not async**: routers are plain `def` (SQLAlchemy is sync); `async def` is reserved for middleware/`lifespan`.
- **Settings validation fails loudly**: `config.py::validate_production_settings` (`@model_validator(mode="after")`) raises at startup if production is misconfigured (placeholder secrets, SQLite in prod, `AUTO_CREATE_SCHEMA=true`, non-HTTPS origin, missing OAuth creds, weak ARC token). Follow this pattern for any new required-in-prod setting.
- **Feature flags default off**: new capabilities (new connectors, retrieval, uploads) ship as a `Settings` field defaulting `False`/`[]`, validated strictly only when enabled. See `ARC_RETRIEVAL_ENABLED`, `CONNECTORS_ENABLED`, `CONNECTORS_ALLOW_SCRAPING`.
- **Idempotency / retry-safety**: callers must pass explicit target state rather than relying on an implicit "next step" (e.g. `advance_stage(target)`); task retries rely on lease ownership (`ClaimedTask.ensure_owned`), not pure idempotent handlers.
- **Fail loudly, no placeholders** (hard rule, see Research-Integrity below): on LLM/retrieval failure, record and stop — never write canned/fake output and mark a step done.
- **Pure-logic modules stay dependency-free**: `dedupe.py`, `merge.py`, `search_query.py`, `citation_verifier.py`, `doi.py`, etc. take plain data in/out, no DB/HTTP — keep new business logic in this shape where possible, for pure unit testing.

## Important Files

- App entry: `backend/app/main.py`; worker entry: `backend/app/worker.py` (`run_forever()`)
- Config: `backend/app/config.py` (`Settings`, `.env`-driven, `get_settings()` lru-cached)
- DB: `backend/app/database.py`, models: `backend/app/models.py`, schemas: `backend/app/schemas.py`
- AuthZ: `backend/app/dependencies.py`
- Task queue: `backend/app/task_queue.py` / `task_registry.py` / `task_runner.py` / `task_handlers.py`
- Agent: `backend/app/agent/executor.py`, `backend/app/agent/llm.py`, `backend/app/agent/arc_client.py`
- Prompts: `backend/app/prompt_registry.py` + `backend/app/prompts/*.toml`
- Env template: `backend/../.env.example` (i.e. `research-main/.env.example`)
- Dependencies: `backend/requirements.txt`
- Migrations config: `alembic.ini`, env wiring: `backend/migrations/env.py`
- Docker: `Dockerfile` (single image, two commands: `uvicorn ...` for web, `python -m app.worker` for worker), `docker-compose.yml` (`db` = pgvector/pgvector:pg16, `web`, `worker`)
- Docs: `README.md`, `docs/DEPLOYMENT.md`

## Runtime / Tooling Preferences

- **Python 3.13** in the app's dev venv (`.venv` at the repo root); Docker image uses `python:3.12-slim`.
- **Two separate virtualenvs, do not mix**:
  | Venv | Python | Use for |
  |---|---|---|
  | `.venv` (repo root) | 3.13 | This app — pip install, pytest, uvicorn, Alembic. **Default for all work here.** |
  | `<workspace>/.venv-retrieval` | 3.11 | ARC microservice only. Never run research-main tests in it. |
- Package manager: plain `pip` + `backend/requirements.txt` (pinned versions, e.g. `fastapi==0.115.8`, `sqlalchemy==2.0.38`, `alembic==1.14.1`, `httpx==0.28.1`, `pytest==8.3.4`). No lockfile tool (poetry/uv) in use.
- DB: SQLAlchemy 2.0 sync engine, Postgres (`psycopg[binary]`) in prod/docker, SQLite in dev/tests.
- Migrations: Alembic, linear chain, naming `YYYYMMDD_NNNN_slug.py`, revision id == filename prefix. `backend/migrations/env.py` reads `DATABASE_URL` from `Settings` at runtime — never hardcode a URL in a migration.

## Testing & QA

- Framework: **pytest** (`pytest.ini`: `pythonpath = backend`, `testpaths = backend/tests`). No coverage tool/threshold configured — the only quantitative gate is the raw pass count tracked in `plan.md`'s "Test baseline" field (currently 1112 passed) and in the Work Log after each session; **never merge a change that lowers this count without explanation.**
- Run: `pytest -q` from the repo root `research-main/`.
- `backend/tests/conftest.py`: sets `DATABASE_URL=sqlite+pysqlite://` (in-memory), `AUTO_CREATE_SCHEMA=true`, `RUN_RESEARCH_INLINE=true` **before** importing the app; `isolated_database` fixture (autouse) drops/recreates all tables per test; `fake_llm` fixture fakes only the HTTP layer so the real request-building/parsing code still runs.
- No shared `TestClient`/DB fixture — each test file defines its own small `_login(email)` / `_project(db)` helpers.
- Naming: `test_<area>.py` mirrors `app/<area>.py` 1:1; test function names are full descriptive sentences (`test_without_an_approval_the_gated_task_never_runs_however_often_the_worker_looks`).
- Notable patterns worth reusing:
  - **Role-matrix test** (`test_role_matrix.py`): one `MATRIX` of `(method, path, allowed_roles, success_status)` crossed with every principal type, plus a live coverage guard that fails if a new route lacks a matrix entry.
  - **Connector/LLM tests never hit the network**: `httpx.MockTransport`, same pattern as `fake_llm`.
  - **Adversarial fixture library** (`adversarial_fixtures.py`, no `test_` prefix so it isn't collected): ~21 prompt-injection/hostile-text payloads with `canaries` that must never leak past fencing; consumed by `test_adversarial.py`, `test_arc_fencing.py`, `test_prompt_fencing.py`.
  - **Migration verification is manual, not a pytest test**: up/down/up + `alembic check` against a scratch SQLite DB, result recorded in `plan.md`'s Work Log per task.
  - Every migration implements both `upgrade()` and `downgrade()`; destructive ones add a safety check (e.g. refuse to drop a table that still has rows).

## Research-Integrity Rules (non-negotiable, from `plan.md` §1.6)

These are enforced in code and must not be relaxed by any change:
1. **Gates can't be bypassed** — no code path may auto-approve a gate, approve on a human's behalf, or proceed past `blocked`.
2. **Fail loudly** — on LLM/retrieval failure, record the failure and stop; never write placeholder/canned content and mark a step done.
3. **Retrieved text is untrusted data** — web/PDF/abstract text is fenced and labelled in prompts, never treated as instructions.
4. **Numbers come from code; citations come from verified records** — no generated statistic or reference is stored as verified without a computed source or resolved record.
5. **Every AI output records** `model_id`, `prompt_version`, inputs, and actor.
6. **Never import ARC pipeline code** — call it only through its HTTP retrieval service.

## Workspace Governance (required before any change)

This repo is driven by `plan.md`, the single living task plan — read it before starting work:
1. Pull/re-read `plan.md` fresh (never act on a cached copy).
2. Pick the highest-priority unclaimed task whose deps are `[x]`.
3. **Claim it first** (`[~]`, `owner`, `since: <date>`), save `plan.md`, *then* touch code — a claim is the lock.
4. Status marks: `[ ]` TODO, `[~]` IN-PROGRESS, `[?]` REVIEW (done, needs independent review), `[!]` BLOCKED, `[x]` DONE (verified), `[-]` DROPPED.
5. Discovered work goes into `plan.md` as a new task, not into your diff — don't silently expand scope.
6. `[x]` only after `pytest -q` passes from the repo root `research-main/` and the test baseline hasn't regressed; **no self-review** for P0/P1 — a different agent/developer moves `[?]` → `[x]`.
7. Append one line to `plan.md`'s Work Log per session.
8. Follow `plan.md` §1.8 (rules 33–46) for local task choosing, execution and git: the working tree is shared and often dirty, so never revert or clean changes you didn't make, and never commit, push, branch, stash or reset unless the user explicitly asks for that action. All branch work reaches `main` through one reviewed PR per task, following rules 47–56.
