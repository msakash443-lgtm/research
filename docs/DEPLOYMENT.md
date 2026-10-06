# Production deployment runbook

## Scope

This runbook deploys the existing private research-agent MVP. It does not provision a cloud account, domain, OAuth credential, LLM account, or billing. Those choices must come from the owner before a live deployment is made.

The production topology is deliberately simple:

```text
HTTPS domain / load balancer
              │
              ▼
        web container (FastAPI + static dashboard)
              │
        private managed PostgreSQL ◄── worker container ──► LLM provider API
                                          │
                                          │ HTTPS + bearer token (only if ARC_RETRIEVAL_ENABLED=true)
                                          ▼
                              TLS proxy ─► ARC retrieval service ──► public web / scholar search / PDFs
                                          (separate image, separate repo)
```

Keep the web service and worker separate so a browser request never has to remain open while an agent run is processing.

Only the worker calls the LLM provider and the ARC retrieval service: research runs execute as queued tasks in the worker. (`RUN_RESEARCH_INLINE=true` runs them inside the web process instead; that is for local development without a worker and must stay `false` in production.)

## Pre-flight decisions

Before deploying, choose:

1. Hosting platform (for example, Google Cloud Run, Render, Railway, or another container host).
2. Whether V1 is private to one person or must support multiple users now.
3. LLM provider/model and maximum acceptable spend per day.
4. Domain name and the Google OAuth account/project that will own the credential.
5. Whether automated web/scholar retrieval (the ARC service, below) is wanted. It is off by default; see [ARC retrieval service](#arc-retrieval-service-optional) for what must happen before it is turned on in production.
6. Whether source uploads, Drive, Sheets, or calculation tools are required in V1. They are not enabled by this repository yet.

## Build one image

From the repository root:

```powershell
docker build -t research-ai:latest .
```

The same image supports two commands:

```text
Web:    uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers
Worker: python -m app.worker
```

Run migrations as a one-off release step from the same image before changing web or worker versions:

```text
alembic -c /app/alembic.ini upgrade head
```

Never rely on `Base.metadata.create_all()` in production. Set `AUTO_CREATE_SCHEMA=false`.

## Required production configuration

Place all secrets in the platform's secret manager—not in `.env`, container layers, browser code, CI logs, or the database.

```dotenv
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://<least-privilege-user>:<password>@<private-host>:5432/<database>?sslmode=require
SESSION_SECRET=<random-32-or-more-character-secret>
PUBLIC_ORIGIN=https://research.example.com
ALLOWED_HOSTS=["research.example.com"]
CORS_ORIGINS=[]
AUTO_CREATE_SCHEMA=false

GOOGLE_CLIENT_ID=<oauth-client-id>
GOOGLE_CLIENT_SECRET=<oauth-client-secret>
GOOGLE_REDIRECT_URI=https://research.example.com/api/auth/callback

LLM_API_KEY=<provider-secret>
LLM_API_BASE_URL=https://provider.example/v1
LLM_MODEL=<model-name>
LLM_TIMEOUT_SECONDS=60
RESEARCH_RUN_LIMIT_PER_DAY=20
RUN_RESEARCH_INLINE=false
MAX_REQUEST_BODY_BYTES=1048576

# Worker / task queue (see "Worker, leases and retries")
RESEARCH_WORKER_POLL_SECONDS=2
TASK_LEASE_SECONDS=300
TASK_RETRY_BASE_SECONDS=5
TASK_RETRY_MAX_SECONDS=300

# ARC web/scholar retrieval: leave disabled until the checks in "ARC retrieval service" pass
ARC_RETRIEVAL_ENABLED=false
ARC_RETRIEVAL_BASE_URL=https://arc-retrieval.internal.example
ARC_RETRIEVAL_TOKEN=<shared-secret-of-16-or-more-characters>
ARC_RETRIEVAL_TIMEOUT_SECONDS=120
ARC_MAX_WEB_RESULTS=8
ARC_MAX_SCHOLAR_RESULTS=5
ARC_MAX_CRAWL_URLS=5
```

The web and worker containers need the same configuration (both read it from the environment; `.env` is only for local development).

The application refuses to start in production (`ENVIRONMENT=production`) if:

- `SESSION_SECRET` is a placeholder or shorter than 32 characters;
- `DATABASE_URL` is SQLite;
- `AUTO_CREATE_SCHEMA` is true;
- `PUBLIC_ORIGIN` is not `https://`, or `GOOGLE_REDIRECT_URI` does not start with it;
- the Google OAuth client id or secret is missing;
- `ARC_RETRIEVAL_ENABLED=true` and `ARC_RETRIEVAL_BASE_URL` is not `https://`, or `ARC_RETRIEVAL_TOKEN` is shorter than 16 characters.

`RESEARCH_RUN_LEASE_SECONDS` and `RESEARCH_RUN_MAX_ATTEMPTS` (commented out in `.env.example`) belong to the old run queue. The current worker does not read them; they go away with that code (plan M0.6.6). Use the `TASK_*` settings instead.

### Secrets

| Secret | Needed by | Notes |
| --- | --- | --- |
| `DATABASE_URL` (contains the DB password) | web, worker, migration job | Least-privilege application user. |
| `SESSION_SECRET` | web | Rotating it signs every user out. |
| `GOOGLE_CLIENT_SECRET` | web | |
| `LLM_API_KEY` | worker (and web only if `RUN_RESEARCH_INLINE=true`) | Set provider-side spend limits too. |
| `ARC_RETRIEVAL_TOKEN` | worker **and** the ARC service (same value) | Only when retrieval is enabled. Rotate both sides together. |
| `TAVILY_API_KEY` | ARC service only | Optional; without it ARC uses its keyless search fallback (open question Q7 in `plan.md`). |

## Worker, leases and retries

The worker (`python -m app.worker`) loops: reap expired task leases, release tasks whose gate a person has approved, then claim and run one due task (or sleep `RESEARCH_WORKER_POLL_SECONDS` if none is due).

- A claimed task holds a lease of `TASK_LEASE_SECONDS`. The worker renews it every `TASK_LEASE_SECONDS / 3` while the task runs, so long LLM or retrieval calls are not mistaken for a crash.
- If a worker dies, its lease expires and the next loop of any worker reaps the task and makes it claimable again. A worker that has lost its lease cannot complete the task.
- A failed attempt is retried after `TASK_RETRY_BASE_SECONDS × 2^(attempt − 1)`, capped at `TASK_RETRY_MAX_SECONDS`. When the task's own `max_attempts` is reached (or the error is permanent), the task is marked failed and the failure is recorded; no placeholder result is written.
- Tasks that need a human gate approval stay blocked until a person approves it; the worker never approves gates.
- Keep `TASK_LEASE_SECONDS` well above the renewal interval and network hiccups. The defaults (300 s lease, renewed every 100 s) are fine for the 60 s LLM and 120 s ARC timeouts.
- Several worker replicas may run against the same database: on PostgreSQL a claim locks the task row with `FOR UPDATE SKIP LOCKED`, so two workers never take the same task. Start with one.

## ARC retrieval service (optional)

Automated web/scholar retrieval is provided by the AutoResearchClaw (ARC) retrieval microservice, a **separate deployment** from a separate repository (`AutoResearchClaw-main`). research-main only calls it over HTTP (`POST /v1/retrieve`); it never imports ARC code. It is off unless `ARC_RETRIEVAL_ENABLED=true`, and even then it only runs when a user ticks "Use automated web/scholar retrieval" on a run.

**Build and run** (from the ARC repository root):

```text
Image:   docker build -f Dockerfile.retrieval -t arc-retrieval:latest .
Command: python -m researchclaw.server.retrieval_api   (the image default; listens on ARC_RETRIEVAL_PORT, default 8800)
Health:  GET /api/health   (no token needed)
Env:     ARC_RETRIEVAL_TOKEN   required, 16+ characters; the service refuses to start without it
         TAVILY_API_KEY        optional
         ARC_RETRIEVAL_HOST / ARC_RETRIEVAL_PORT   optional
```

**Network**

- The service speaks plain HTTP, but research-main requires an `https://` base URL in production. Put it behind a TLS-terminating proxy or the platform's internal HTTPS ingress.
- Allow ingress from the research-main worker only. Do not expose it publicly: it fetches arbitrary web pages on request.
- It needs outbound internet access (search APIs, crawled pages, PDFs). It needs no database and no LLM credentials; do not give it any.
- research-main calls it with redirects disabled and a timeout of `ARC_RETRIEVAL_TIMEOUT_SECONDS`, and asks for at most the `ARC_MAX_*` results. The service also enforces its own hard caps (10 web results, 5 scholar results, 5 crawled URLs, 20 items per kind, 6,000 characters per field).

**What research-main does with the results**

- Retrieved items are saved as automated sources with `metadata_verified=false`, ranked below researcher-entered sources, and labelled "Auto-retrieved, unverified" in the UI.
- Their text is treated as untrusted data: cleaned and fenced in the prompt, never followed as instructions.
- If ARC fails or is unreachable, the run continues without it and the retrieval failure is recorded on the run and in the audit log.

**Before enabling it in production** (status on 2026-10-03; see `plan.md`):

- [ ] M1.1.8: service smoke test (`curl POST /v1/retrieve`). Not run yet; waiting on Q7 (Tavily or the keyless fallback).
- [ ] M1.1.10: end-to-end smoke with both services.
- [ ] M1.1.11: ARC hardening (SSRF re-check on redirects, response-size caps, writable cache dir for the non-root user).
- [ ] X.1.2: ARC retrieval tests in CI.

Until these are done, keep `ARC_RETRIEVAL_ENABLED=false` in production.

## Database and network

- Use managed PostgreSQL with automated backups and point-in-time recovery enabled.
- Create a dedicated least-privilege application user; do not use the database administrator account.
- Restrict database ingress to the web/worker service identity or private network. Do not give it a public IP.
- Require TLS from the application to the database.
- Test a restore before relying on backups.
- pgvector is available in the development image, but no vector index is needed for this MVP. Introduce it only when an evaluated retrieval use case requires it.

## OAuth and browser security

- Register the exact `PUBLIC_ORIGIN` and `/api/auth/callback` URI in Google Cloud.
- Use HTTPS everywhere. Production session cookies are marked secure.
- Configure only the production hostname in `ALLOWED_HOSTS`.
- Keep `CORS_ORIGINS=[]` while the dashboard and API remain same-origin.
- Do not enable the development-login route in production; it is automatically unavailable there.

## Release checklist

1. Run `pytest -q` and build the container image.
2. Apply the database migration as a release job. CI applies every migration to a real PostgreSQL (upgrade, `alembic check`, full downgrade and upgrade again) and runs the task-queue concurrency tests there (plan X.1.1); still apply new migrations to a staging copy of the production database first, since CI starts from an empty database.
3. Deploy one web service and one worker service from the same image.
4. Configure health checks: `/health` for liveness and `/ready` for database readiness.
5. Verify Google sign-in and sign-out on the HTTPS domain.
6. Create a test project, save a source excerpt, and start one research run.
7. Confirm that the worker completes the run and that a model error is safely shown without leaking credentials.
8. Set cost/usage alerts at the LLM provider and platform levels.
   If ARC retrieval is enabled: check `GET /api/health` on the ARC service, then start one run with retrieval ticked and confirm the new sources appear as "Auto-retrieved, unverified".
9. Enable application/platform error logging with redaction for secrets and source content.
10. Document rollback: retain the prior image and use backward-compatible migrations.

## Safeguards before adding more agent tools

Do not add unrestricted browsing, URL fetching, code execution, or uploads directly to the web process.

| Capability | Required control before enabling it |
| --- | --- |
| Web fetching | Only through the ARC retrieval service, never in the web process. SSRF protection, DNS/IP checks, redirect/content-type/size limits, safe HTML/PDF extraction, provenance capture (open ARC gaps: M1.1.11) |
| Search | Only through the ARC retrieval service. Approved provider (Q7), query/rate/cost limits, result provenance, explicit source-quality rules |
| File uploads | Private object storage, MIME/size validation, malware scanning, retention/deletion policy |
| Python analysis | Isolated job runtime with no credentials or network, CPU/memory/time/package limits |
| Google Drive/Sheets | Minimal OAuth scopes, encrypted refresh-token storage, per-user authorization and revocation |
| Multi-user/BYOK | Tenant isolation, encryption/KMS, quotas, audit logs, billing/abuse controls, security review |

## Platform mapping

Any container platform can host this topology. A natural option when using Google OAuth is a container web service plus a separate worker service, a managed PostgreSQL instance, and secret manager. If selecting a different host, the container image and environment contract remain the same.
