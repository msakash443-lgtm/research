# Localhost diagnostic and end-to-end error log

Date: 2026-11-11 (Asia/Calcutta). Task: X.227. Tester: @codex.

**Result: blocked before live end-to-end workflows. The website is not verified working.** The frontend logic suite passed, but the configured backend could not start and the PostgreSQL test prerequisite was missing. No application fixes, commits, pushes, merges, or branch changes were performed.

## Version and environment

- Checkout tested: `Revathi`, commit `27466d70e8e1954a201f595d5e95fda54c9f0082`.
- This differs from the `main` checkout observed during the earlier Git audit; the branch changed outside this diagnostic's actions. No pull or sync was performed. Results apply to the local commit above, not the newer GitHub `main`.
- Application interpreter: repository `.venv/Scripts/python.exe`.
- Target: `http://127.0.0.1:8000/`.
- Application Settings validated; environment is development, database is PostgreSQL, `auto_create_schema=true`, and inline research execution is enabled.
- The configured database host is the Docker service name `db`. No credentials or secret-file contents are included here; `.env` was not inspected or edited. The application loaded its existing configuration normally.

## Flagged errors

### E01 — Blocker: direct Windows startup cannot resolve the configured database

Reproduction, from the repository root:

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The attempt outside the sandbox reached normal application startup, then exited with code 1. Relevant output:

```text
INFO:     Started server process
INFO:     Waiting for application startup.
psycopg.OperationalError: [Errno 11001] getaddrinfo failed
sqlalchemy.exc.OperationalError: (psycopg.OperationalError) [Errno 11001] getaddrinfo failed
ERROR:    Application startup failed. Exiting.
```

The failure occurred in `backend/app/main.py` lifespan while `Base.metadata.create_all(bind=engine)` attempted to connect. `db` is the service hostname in `docker-compose.yml`; it is not resolvable from this host-run process. This is a runtime configuration/startup blocker, not evidence of a defect in a research workflow.

Expected: normal lifespan completes, the API listens on port 8000, and both `/health` and `/ready` succeed. Follow-up: start the intended Compose services with Docker available, or explicitly configure a reachable host PostgreSQL instance following `docs/LOCAL_POSTGRES_MIGRATION.md`. Existing databases were not migrated or reset.

### E02 — Blocker: Docker access could not be verified

Read-only `docker ps --format ...` and `docker version --format ...` failed with exit code 1:

```text
permission denied while trying to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

An elevated read-only retry was requested, then cancelled by the user. No successful engine/container-state result was obtained, so this report does not claim Docker itself is broken or stopped. No Compose services or containers were started by this diagnostic.

### E03 — Blocker: backend test suite cannot load without TEST_DATABASE_URL

Command attempted from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Actual pytest exit code: **4**. No tests executed. Output:

```text
ImportError while loading conftest '...backend/tests/conftest.py'.
backend/tests/conftest.py:17: in <module>
    raise pytest.UsageError(
E   _pytest.config.exceptions.UsageError: TEST_DATABASE_URL is not set.
```

Full output: [localhost-e2e-2026-11-11-pytest-initial.txt](localhost-e2e-2026-11-11-pytest-initial.txt).

Expected: a dedicated throwaway PostgreSQL test configuration, with a unique database prefix ending `_test` and a role permitted to create test databases. The existing harness clones/drops its derived test databases; it must not target the live app database or share a prefix with another concurrent test run. The documented Windows serial fallback was not useful here because configuration failed before test collection, not because xdist crashed. No backend pass count is claimed, and the plan's prior baseline is unchanged.

### E04 — Live symptom: browser shows only an unstyled shell

The in-app browser opened `http://127.0.0.1:8000/`. Both the initial observation and an explicit reload showed only the `Research AI` link, with no login, project list, or workspace controls. The screenshot showed a plain white page and the unstyled link. Captured warning/error console logs were empty; that is not evidence that assets or initialization succeeded.

Subsequent HTTP checks outside the sandbox returned connection refused for all five targets:

| Target | Actual result |
|---|---|
| `/health` | WinError 10061: connection refused |
| `/ready` | WinError 10061: connection refused |
| `/` | WinError 10061: connection refused |
| `/assets/styles.css` | WinError 10061: connection refused |
| `/assets/app.js` | WinError 10061: connection refused |

The visible shell does not demonstrate that a server is running. Source inspection confirms that `index.html:16` contains the brand, while the auth/workspace sections start hidden (`index.html:31,46`). The entry module imports `/assets/js/**`; `sw.js:1-14` provides a cache fallback for the shell but does not pre-cache that full module graph. **Inference only:** cached shell content could explain the browser display while the backend is unavailable. Cache involvement and the exact browser asset failure were not confirmed. This symptom is not counted as a separately proven frontend regression.

## Checks completed and coverage limits

| Check | Result |
|---|---|
| Frontend Node suite | **PASS: 240 tests, 0 failures, 0 skips; exit 0** |
| Application Settings validation | Passed; database reachability is a separate failure |
| Normal backend startup | Failed: E01 |
| Health, readiness, root HTML, CSS and entry JS over HTTP | Failed: connection refused |
| Browser initial load and explicit reload | Incomplete shell only: E04 |
| Full backend pytest | Blocked before collection: E03 |
| Development login and session persistence | Not reached |
| Project creation, Scope, criteria and reload persistence | Not reached |
| Sources, excerpts, search, screening and evidence workflows | Not reached |
| Worker/research execution, exports and audit reads | Not reached |
| Gate refusal, role matrix and research-integrity checks | Not exercised live; no gate was approved or bypassed |
| AI/retrieval integrations and external models | Not exercised |
| Responsive/mobile layout | Not meaningfully testable from the incomplete shell |

Frontend command:

```powershell
node --test backend/app/web/js/test/*.test.mjs
```

Recorded duration: approximately 21.6 seconds. Full output: [localhost-e2e-2026-11-11-node.txt](localhost-e2e-2026-11-11-node.txt). These are frontend logic tests, not a replacement for browser/database end-to-end coverage. No tests were added because this task changes documentation only.

## Diagnostic side effects and final state

- Sandbox-only localhost requests initially failed with WinError 10013. Repeating outside the sandbox established the real result: connection refused. The sandbox restriction is not logged as an application defect.
- A first sandbox `initdb` invocation eventually initialized a new, unused PostgreSQL 18 cluster at `data/localhost-e2e-20261111-227/`. It contains no imported user data and was **not started**. The separate elevated initialization request was declined and was not retried; its proposed `...-host` cluster was not created by that rejected command. The initialized scratch directory is ignored by Git and retained; nothing was deleted.
- The elevated app startup exited with code 1. The original sandbox startup attempt was cancelled. No functioning web server or worker was left running by this diagnostic.
- No user research data, secret files, or existing database schema were changed. No live project or test account was created.
- Existing untracked `local_e2e.cookies` was left untouched and unread.
- Documentation added/updated: this report, the two test-output files, own X.227/X.228 task and Work Log entries in `plan.md`, and the related traceability row.

## Follow-up

X.228 tracks restoring an authorized, reachable local PostgreSQL/Compose runtime and dedicated test configuration. Once that prerequisite is available, repeat normal startup and health/readiness checks, then the browser workflow matrix and full PostgreSQL suite. X.227 remains blocked; this report does not certify end-to-end success.

Suggested eventual documentation commit message (not executed): `X.227: record localhost diagnostic blockers and test results`.
