# Local PostgreSQL migration

The application already supports PostgreSQL as its canonical runtime database:

- Docker Compose runs `pgvector/pgvector:pg16` and injects a `postgresql+psycopg://...@db:5432/...` URL into both web and worker.
- Alembic reads `DATABASE_URL` from settings (`backend/migrations/env.py`).
- Tests intentionally override `DATABASE_URL` to in-memory SQLite and must remain isolated from developer databases.

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

## Preserving an existing SQLite database

Do not point the application at PostgreSQL until the data copy has been checked. The repository contains multiple scratch SQLite files; they are not a reliable source of application data. Select the one database explicitly and make a backup copy outside the repository.

A safe cutover is:

1. Stop the API and worker.
2. Back up the selected SQLite file and create a fresh PostgreSQL database.
3. Apply all Alembic migrations to PostgreSQL.
4. Copy application rows using a reviewed data-migration tool or a one-off SQLAlchemy script that preserves primary keys, foreign keys, timestamps, JSON values, and append-only records. Do not copy SQLite's `alembic_version` row; Alembic owns that table on the destination.
5. Reset PostgreSQL sequences after the copy, then run row-count and foreign-key consistency checks for every copied table.
6. Run the application against PostgreSQL and verify login, project creation, source/excerpt persistence, audit reads, and one worker task before deleting or changing the SQLite file.
7. Keep the SQLite backup until the PostgreSQL verification and rollback window are complete.

This repository does not include an automatic SQLite-to-PostgreSQL copier because silently choosing one of the local `.db` files or copying integrity-sensitive tables without an explicit source database would risk data loss. The PostgreSQL server/client was not available in this environment, so the live migration and Postgres smoke checks remain unexecuted.
