# Local PostgreSQL migration

The application uses PostgreSQL as its only runtime database:

- Docker Compose runs `pgvector/pgvector:pg16` and injects a `postgresql+psycopg://...@db:5432/...` URL into both web and worker.
- Host-installed runs must set `DATABASE_URL` to a PostgreSQL URL.
- Alembic reads `DATABASE_URL` from settings (`backend/migrations/env.py`).
- The test suite also runs on PostgreSQL; see [Running the tests](#running-the-tests). SQLite is not supported anywhere.
## New local PostgreSQL installation

1. Create a database and role in PostgreSQL. Keep the password out of the repository.
2. Copy `.env.example` to `.env` and set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` to the values created above.
3. For a host-installed PostgreSQL server, set this in `.env`:

   ```dotenv
   DATABASE_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:5432/<database>
   AUTO_CREATE_SCHEMA=false
   ENVIRONMENT=development
   ```

   URL-encode reserved characters in the password. `DATABASE_URL` takes precedence over the Compose service URL when running the API directly on the host.
4. Apply the schema:

   ```powershell
   .venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
   .venv\Scripts\python.exe -m alembic -c alembic.ini check
   ```

5. Start the API and worker with the same `.env` so both processes use PostgreSQL.

## Verification

After applying the schema, start the API and worker with the same PostgreSQL configuration and verify:

1. `/health` and `/ready` return success.
2. Development login and project creation work.
3. Source/excerpt persistence and audit reads work.
4. One worker task completes against PostgreSQL.

The owner decided on 2026-11-10 that historical SQLite data and its migration are not required (X.148). Set up a new PostgreSQL database as above; the application has no SQLite runtime fallback or automatic data copier. Do not delete an existing database or backup as part of this setup.

## Running the tests

`pytest` needs a throwaway PostgreSQL server where one role may `CREATE DATABASE`. It never reads `DATABASE_URL` or `.env`. Start one next to the dev database (pick your own password; the data lives in memory and is lost when the container stops):

```powershell
docker run -d --name research-pg-test -e POSTGRES_USER=research_test -e POSTGRES_PASSWORD=<pick-a-password> `
  -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data pgvector/pgvector:pg16 `
  postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200
$env:TEST_DATABASE_URL = "postgresql+psycopg://research_test:<pick-a-password>@127.0.0.1:55432/research_ai_test"
pytest -q
```

`TEST_DATABASE_URL` must name a database ending in `_test`; that name is only a prefix. `backend/tests/conftest.py` builds `<name>_<worker>_tpl` once per pytest process from the models and keeps one working database `<name>_<worker>` per process. A test that waits more than 10 seconds for a row lock fails instead of hanging.

### How each test is isolated (X.235)

- **Rollback (the default).** The test runs inside an outer transaction on one connection; `SessionLocal` joins it through savepoints, so every `commit()` the test or the app makes is undone when the test ends. This costs about a millisecond instead of a `CREATE DATABASE` (about two seconds on Windows). A test that opens a connection of its own (`engine.connect()`, a second `sessionmaker`, the app lifespan) is failed with a message telling you to mark it, because it would leak committed rows into later tests.
- **Clone.** Tests marked `@pytest.mark.real_db` (and any test using the `race_db` fixture) get a working database cloned from the template first. Use it for threads and second connections, lock contention, `FOR UPDATE SKIP LOCKED` races, and anything that needs a real `COMMIT` to be seen elsewhere. `test_guards_postgres.py`, `test_task_queue_postgres.py` and `test_dashboard.py` are marked as a whole.
- **Alembic tests** (`migrator`, `scratch_database_url`) work on a separate scratch database. `migrator` starts an upgrade from an empty scratch database from the nearest revision this worker already migrated to, so each revision is only walked once per worker; `test_migration_chain.py` (marked `slow`) runs `upgrade head`, `check` and `downgrade base` for real.
- `RESEARCH_TEST_ISOLATION=clone pytest -q` forces the clone mode for every test, to compare the two modes or to rule rollback out when a test fails strangely.
- `now()` is fixed at transaction start, so the template is built with a `public.now()` that returns the clock time (the test connections put `public` ahead of `pg_catalog` in `search_path`). Without it every row a rollback-mode test writes would share one timestamp.
- Sequences are not rolled back, so ids differ from test to test; never assert a literal generated id.

### Parallel runs

`pytest.ini` runs `-n auto --dist loadfile`: one worker per CPU, each test file on one worker (the role-matrix module seeds its world once per file). Each worker holds a few connections (its pool is 5 plus 10 overflow at most, one in rollback mode), so start the server with `max_connections` of at least 200 (the command above does) when you have 8 or more workers; the default of 100 is enough for fewer.

On Windows the xdist workers have crashed with `INTERNALERROR`/`WinError` before. When that happens run serially: `pytest -q -n0 -o addopts=""`. While iterating, add `-m "not slow" --lf`; run the whole suite before merging.
