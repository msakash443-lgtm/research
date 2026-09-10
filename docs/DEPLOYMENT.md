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
        private managed PostgreSQL ◄── worker container
                                  │
                             LLM provider API
```

Keep the web service and worker separate so a browser request never has to remain open while an agent run is processing.

## Pre-flight decisions

Before deploying, choose:

1. Hosting platform (for example, Google Cloud Run, Render, Railway, or another container host).
2. Whether V1 is private to one person or must support multiple users now.
3. LLM provider/model and maximum acceptable spend per day.
4. Domain name and the Google OAuth account/project that will own the credential.
5. Whether source uploads, Drive, Sheets, search, or calculation tools are required in V1. They are not enabled by this repository yet.

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
RESEARCH_WORKER_POLL_SECONDS=2
MAX_REQUEST_BODY_BYTES=1048576
```

The application fails fast if a production session secret is a placeholder, the database is SQLite, schema auto-creation is enabled, HTTPS is missing, or Google OAuth is incomplete.

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
2. Apply the database migration as a release job.
3. Deploy one web service and one worker service from the same image.
4. Configure health checks: `/health` for liveness and `/ready` for database readiness.
5. Verify Google sign-in and sign-out on the HTTPS domain.
6. Create a test project, save a source excerpt, and start one research run.
7. Confirm that the worker completes the run and that a model error is safely shown without leaking credentials.
8. Set cost/usage alerts at the LLM provider and platform levels.
9. Enable application/platform error logging with redaction for secrets and source content.
10. Document rollback: retain the prior image and use backward-compatible migrations.

## Safeguards before adding more agent tools

Do not add unrestricted browsing, URL fetching, code execution, or uploads directly to the web process.

| Capability | Required control before enabling it |
| --- | --- |
| Web fetching | SSRF protection, DNS/IP checks, redirect/content-type/size limits, safe HTML/PDF extraction, provenance capture |
| Search | Approved provider, query/rate/cost limits, result provenance, explicit source-quality rules |
| File uploads | Private object storage, MIME/size validation, malware scanning, retention/deletion policy |
| Python analysis | Isolated job runtime with no credentials or network, CPU/memory/time/package limits |
| Google Drive/Sheets | Minimal OAuth scopes, encrypted refresh-token storage, per-user authorization and revocation |
| Multi-user/BYOK | Tenant isolation, encryption/KMS, quotas, audit logs, billing/abuse controls, security review |

## Platform mapping

Any container platform can host this topology. A natural option when using Google OAuth is a container web service plus a separate worker service, a managed PostgreSQL instance, and secret manager. If selecting a different host, the container image and environment contract remain the same.
