# Research AI Platform

An evidence-first research-agent workspace. It is designed for a researcher to keep a question, decisions, sources, short evidence excerpts, and each agent run in one auditable project.

## What works now

- A same-origin FastAPI web application: one service serves the dashboard and `/api`.
- Google OAuth for production and a deliberately development-only local sign-in.
- Private, user-owned projects and structured research context (questions, hypotheses, methods, variables, and decisions).
- An evidence library with source metadata, an optional URL, a locatable excerpt/data note, and an immutable excerpt hash.
- Durable research runs. Each run stores its question, plan, source/context snapshot, status, output, and configured model identifier.
- A constrained evidence-synthesis agent. It refuses to make a source-grounded answer without evidence excerpts and prompts an LLM to cite source markers such as `[S1]`.
- A searchable project article library. Saved source metadata is returned as APA-style references, and research runs request a structured literature review with an APA references section.
- A separate database-backed worker for durable processing in Docker/production.
- A prompt-driven, backend-only data analysis engine for CSV/Excel uploads, including descriptive statistics, frequencies, data-quality checks, correlations, paired t-tests, OLS regression, and Oaxaca–Blinder decomposition.
- A PDF literature-review page that extracts selectable text, requires APA or MLA selection, and generates a critical review through the configured LLM without treating PDF text as instructions.
- Alembic migrations, production configuration checks, trusted-host protection, secure session-cookie defaults, security headers, health/readiness endpoints, and a per-project daily run cap.

## Current boundary

This is a safe V1 evidence-synthesis agent, not an unrestricted autonomous browser or code executor.

- It does **not** scrape arbitrary URLs, run arbitrary Python/shell code, access Drive/Sheets, accept file uploads, or automatically treat search snippets as verified evidence.
- Add sources and short excerpt/data notes first; search the saved library from the dashboard, and the agent uses only that saved evidence plus project context.
- LLM synthesis is optional. With no `LLM_*` configuration, the UI still saves projects, sources, plans, and runs, but a run reports that synthesis needs configuration instead of fabricating an answer.

That boundary is intentional: web retrieval, uploads, and calculation tools need additional SSRF, prompt-injection, sandbox, storage, and provenance controls before they should be enabled.

## Architecture

```text
Browser
  │ HTTPS, same origin
  ▼
FastAPI web service ──────► Managed PostgreSQL
  │                               ▲
  │ creates durable run            │ stores projects, evidence, runs
  ▼                               │
Research worker ─► configured LLM ┘
```

For a private first deployment, run the web service and worker as two processes from the same image, with a private managed PostgreSQL database. Do not expose the database to the internet.

## Run locally

### Docker (recommended)

1. Install Docker Desktop.
2. Copy the example configuration:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Change `POSTGRES_PASSWORD` and `SESSION_SECRET` in `.env` to local-only values.
4. Start the web service, worker, and local PostgreSQL:

   ```powershell
   docker compose up --build
   ```

5. Open [http://localhost:8000](http://localhost:8000). While `ENVIRONMENT=development`, choose the local development account.

The Docker worker processes queued research runs. For a non-Docker local run, set `RUN_RESEARCH_INLINE=true` and start the API with:

```powershell
python -m pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend
```

## Configure the local model

The default local setup uses Ollama through its OpenAI-compatible `chat/completions` endpoint. Install Ollama, then run:

```powershell
ollama serve
ollama pull llama3.2:3b
```

The project `.env` is configured for:

```dotenv
LLM_API_KEY=ollama
LLM_API_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=llama3.2:3b
LLM_TIMEOUT_SECONDS=120
```

The server sends a low-temperature, evidence-only prompt. Source excerpts are treated as untrusted material, and the prompt instructs the model not to follow instructions found inside them. This lowers risk; it does not replace human review. A remote OpenAI-compatible provider can still be used by changing these values.

## Local accounts

The development page now supports local email/password registration and login. Passwords are stored as scrypt hashes, never plaintext. Existing development-only login remains available for automated tests but is not shown in the UI.

## Google sign-in

Create a Google OAuth **Web application** credential. For local development, use:

- Authorized JavaScript origin: `http://localhost:8000`
- Authorized redirect URI: `http://localhost:8000/api/auth/callback`

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI` in `.env`. In production, use the deployed HTTPS origin and exact callback URL. The browser receives only a signed session cookie; provider credentials remain on the server.

## Database migrations

Development can bootstrap a new schema with `AUTO_CREATE_SCHEMA=true`. A production deployment must set it to `false` and run a versioned migration before starting web/worker services:

```powershell
alembic -c alembic.ini upgrade head
```

Do not run test fixtures against a shared or production database. The test configuration always forces an in-memory SQLite database before importing the app.

## Deploy

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the provider-neutral release checklist and the exact configuration required for a production environment.

## Test

```powershell
pytest -q
```

The tests cover account identity reuse, project ownership, dashboard flow, source/excerpt persistence, and research-run safeguards.

## API overview

| Endpoint | Purpose |
| --- | --- |
| `POST /api/projects` | Create a private research project |
| `POST /api/projects/{id}/context` | Save a structured research decision |
| `GET/POST /api/projects/{id}/sources` | List or create auditable evidence sources |
| `GET /api/projects/{id}/sources/search?q=...` | Search saved article/source metadata and evidence |
| `POST /api/projects/{id}/analysis/datasets` | Upload a CSV or Excel dataset |
| `GET /api/projects/{id}/analysis/datasets` | List project datasets |
| `POST /api/projects/{id}/analysis/runs` | Run an allowlisted statistical analysis from a prompt |
| `POST /api/projects/{id}/literature-review/documents` | Upload and extract text from a PDF research paper |
| `GET /api/projects/{id}/literature-review/documents` | List uploaded literature PDFs |
| `POST /api/projects/{id}/literature-review/generate` | Generate an APA or MLA literature review |
| `GET/POST /api/projects/{id}/research-runs` | Inspect or queue evidence-synthesis runs |
| `GET /health` | Liveness probe |
| `GET /ready` | Database-aware readiness probe |

Interactive API documentation is available at `/docs` while developing.
