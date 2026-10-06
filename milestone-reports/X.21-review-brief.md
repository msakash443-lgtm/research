# X.21 technical-debt cleanup — reviewer brief

**Author:** @claude (all X.21 subtasks). **Needed:** a reviewer other than the author (plan.md §1). Self-reviews done so far don't count.
**Status:** 14 subtasks `[?]` (needs review), 1 dropped (X.21.6). Nothing is committed (user rule: no commits/pushes).
**Baseline:** `pytest -q` from `research-main/research/` → 506 passed on 2026-10-03.

All paths are under `research-main/research/backend/`. Mark each subtask `[x]` in `plan.md` when you're satisfied, or leave it `[?]` and add a note saying what's wrong.

## How to review

1. Run `pytest -q` from `research-main/research/`. It should pass with no failures.
2. For each subtask below, read the files listed and check the "Check" points.
3. Things to look for in general: behaviour changes hidden inside refactors, response fields or error strings that changed, and imports that create cycles.

## Subtasks

### X.21.1 — one way to load excerpts (P2)
- **Files:** `app/models.py` (`Source.excerpts` now has `order_by=SourceExcerpt.created_at`), `app/routers/sources.py` (`list_sources` uses `selectinload`, `_load_source` re-reads with `populate_existing` after create/verify), `app/agent/executor.py` (`selectinload` in the source query).
- **Check:** excerpts come back in the same order as before (`created_at` ascending; the API shows the last one). Create and verify still return the latest excerpt. No lazy loads happen in the worker.
- **Known subtlety:** if a `Source` object stays alive in the session with an empty excerpt list already loaded, `selectinload` won't refresh it. That can't happen today, because nothing keeps new sources alive after ingest. X.21.11 guards it.

### X.21.2 — one hash helper (P2)
- **Files:** `app/models.py::excerpt_hash`, used in `routers/sources.py::create_source` and `executor.py::_ingest_arc_sources`.
- **Check:** gives the same result as `hashlib.sha256(content.encode("utf-8")).hexdigest()`. Existing tests assert this.

### X.21.3 — one clock helper and `Project.touch()` (P3)
- **Files:** `app/models.py` (`utcnow`, `Project.touch`), `routers/{projects,sources,research_runs}.py`.
- **Check:** behaviour is the same. `updated_at` also has `onupdate=func.now()`.

### X.21.4 — one "automated source" rule (P3; done in X.21.10)
- **Files:** `app/models.py::Source.is_automated` (a hybrid property: Python and SQL), `executor.py` (ranking `case((Source.is_automated, 1), else_=0)`, snapshot flag, sort key).
- **Check:** the compiled SQL equals the old `and_(source_type IN ARC types, metadata_verified IS false)`.
- **Known edge:** the Python and SQL versions differ only when `metadata_verified` is `None`. That only happens on unsaved objects (the column is NOT NULL), and the error is on the safe side: the source is treated as automated.

### X.21.5 — no duplicated session-user lookup (P2)
- **Files:** `app/dependencies.py` (`session_user`, `current_user`), `app/routers/auth.py::me`.
- **Check:** every 401 `detail` string and branch is unchanged:
  - stale session → "Session user no longer exists"
  - no credentials, or the `X-User-Id` header outside development → "Authentication required"
  - unknown `X-User-Id` in development → "Unknown user"
  - a stale session plus a valid dev header → returns the header's user
  - `/auth/me` with a stale session clears it and returns "Not signed in"

### X.21.6 — DROPPED
- Gate-approver role sets (`app/gates.py`) are kept separate from route-access sets (`app/dependencies.py`) on purpose; the alias was reverted. **Check:** `app/gates.py` defines its own frozensets and doesn't import from `dependencies`.

### X.21.7 — `main.py` cleanup (P3)
- **Files:** `app/main.py`.
- **Check:** the removed session-secret check is covered by `Settings.validate_production_settings` (`config.py`), which is stricter (also rejects secrets shorter than 32 characters). `settings` is now defined before `lifespan` uses it.

### X.21.8 — out-of-date comments, unused arguments (P3)
- **Files:** `app/models.py` docstrings (`ProjectMember`, `AuditEvent`, the guard-import comment), `executor.py::build_plan(sources)` (the unused `question`/`context` arguments are gone; that part was done in X.21.10).
- **Check:** the comments match the code. `tests/test_inline_mode.py` monkeypatches `build_plan` with `*args`, so it still works.

### X.21.9 — removed the `Claim`/`ClaimEvidence` models (P3, user decision)
- **Files:** `app/models.py` (removed), `migrations/versions/20261003_0013_drop_claims.py`.
- **Check:**
  - The upgrade drops `claim_evidence`, `claims` and the `claim_status` type, and **refuses if either table has rows**. The check runs online only; offline `--sql` mode skips it.
  - The downgrade recreates the original schema from `0001` and creates `claim_status` only once (compare M0.5.8).
  - Verified on scratch SQLite: up → down → refuses with a row present → up.
  - Postgres SQL was generated offline only, never executed (see X.1).
  - Single migration head (now `0016`; `0014` builds on `0013`).

### X.21.10 — executor, gates router and task handler leftovers (P3)
- **Files:** `executor.py`, `routers/gates.py`, `task_handlers.py` (all use `models.utcnow`; the gates router uses `project.touch()`), plus the X.21.4 and X.21.8 items above.
- **Check:** there's no local `utcnow` in `executor.py`. The `routers/gates.py` conditional `UPDATE` (from M0.5.9) is unchanged apart from `decided_at=utcnow()`.

### X.21.11 — test: a retrieved excerpt reaches the run's evidence (P2)
- **Files:** `tests/test_arc_retrieval.py::test_retrieved_excerpt_reaches_the_runs_evidence`.
- **Check:** a run whose only source is retrieved ends as `needs_configuration`, not `needs_sources`, and the snapshot holds the excerpt, locator and `automated`.
- **Mutation check (repeatable):** in a throwaway test, `event.listen(Source, "init", ...)` sets `target.excerpts = []` **and keeps a strong reference to the target**. The test then fails with `needs_sources`. Without the strong reference the mutant survives, because the identity map holds objects weakly.

### X.21.12 — the models no longer depend on the agent code (P3)
- **Files:** `app/models.py` (`ARC_SOURCE_TYPES` is defined here), `app/agent/arc_client.py` (constant removed).
- **Check:** `models.py` imports nothing from `app` except `app.database` and its guard modules. `alembic heads` still loads the models.

### X.21.13 — last two clock calls (P3)
- **Files:** `app/task_queue.py` (`_now()` removed), `app/worker.py`.
- **Check:** `grep -rn "datetime.now(" app` → only `models.py`. No test patched `_now`.

### X.21.14 — one rule end to end, including the UI (P3)
- **Files:**
  - `app/schemas.py` (`SourceRead.is_automated`, required)
  - `routers/sources.py::source_response` (the only place that builds `SourceRead`)
  - `app/web/app.js` (the badge uses `source.is_automated`; its own type list is gone)
  - `agent/arc_client.py` (a comment links to the shared list)
  - two tests in `test_arc_retrieval.py`
- **Check:** every `source_type` that `_normalize` produces is in `ARC_SOURCE_TYPES`, and the API flag goes true → false on verify.
- **Browser check done:** in the dev app the badge shows only on a `scholar` source.

### X.21.15 — hand-entered sources can't use retrieval types (P3)
- **Files:** `app/schemas.py::SourceCreate.reject_retrieval_types`, `tests/test_arc_retrieval.py::test_manual_sources_cannot_use_retrieval_types` (5 cases).
- **Check:**
  - The four ARC types are rejected with 422, ignoring capitals and spaces, and nothing is saved.
  - The UI form only offers non-ARC types.
  - There's no other endpoint that writes `source_type`.
  - Existing rows aren't changed.

## Open items, not part of this review
- **M1.3.1** has a note: connectors will need an explicit, system-set `Source.origin` instead of guessing it from the type name.
- **Dev DB:** it contains a test project "X.21.14 badge check" (user `x21-check@example.com`) with a `scholar`-typed source created before X.21.15. You can delete it.
