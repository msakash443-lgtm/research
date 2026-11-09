# Local PostgreSQL migration

The application uses PostgreSQL as its only runtime database:

- Docker Compose runs `pgvector/pgvector:pg16` and injects a `postgresql+psycopg://...@db:5432/...` URL into both web and worker.
- Host-installed runs must set `DATABASE_URL` to a PostgreSQL URL.
- Alembic reads `DATABASE_URL` from settings (`backend/migrations/env.py`).
- The test harness is separately isolated and is not an application runtime.
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

The application does not include a SQLite-to-PostgreSQL runtime fallback or automatic data copier. Any historical data transfer must use an explicitly selected source, a reviewed migration procedure, and row-count/foreign-key verification before cutover.
