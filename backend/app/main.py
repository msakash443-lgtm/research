import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.database import Base, engine
from app.source_gate_guard import ExtractionGateLocked
from app.stage_order import StageNotInChain
from app.config import get_settings
from app.logging_config import configure_logging
from app.middleware import ContentLengthLimitMiddleware, FixedWindowCounter, RateLimitMiddleware
from app.pagination import NEXT_CURSOR_HEADER
from app.routers import accuracy, ai_locks, ai_settings, audit, provenance_reports, workspace_ai, auth, bibliometrics, claims, clusters, connectors, coverage, criteria, design_tools, evidence_matrix, exports, extraction_schemas, extractions, gates, guide, integrity, members, notes, documents, prisma, profile, projects, replication, research_runs, scholar_metrics, screening, searches, alerts, seeds, snowball, sources, stage, suggestions, usage, zotero, reviews
from app.routers import journals, submissions
from app.routers import privacy
from app.routers import observability
from app.routers import bias_warnings
from app.routers import analysis_plan, brief, conceptual_model, constructs, datasets, ethics_pack, gaps, idea_brief, instrument, interpretation, meta_analysis, preregistration, qual_coding, research_questions, transcripts

# Production settings (session secret, HTTPS, Postgres, ...) are validated in Settings.
settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    # Local convenience only. Production releases must apply Alembic migrations first.
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield



app = FastAPI(
    title="Research AI Platform",
    version="0.2.0",
    lifespan=lifespan,
    # API docs are helpful locally but should not expose an exploration surface on the public deployment.
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
)


@app.exception_handler(ExtractionGateLocked)
def extraction_gate_locked_handler(request, exc: ExtractionGateLocked):
    return JSONResponse(status_code=409, content={"detail": str(exc)})
@app.exception_handler(StageNotInChain)
def stage_not_in_chain_handler(request, exc: StageNotInChain):
    return JSONResponse(status_code=409, content={"detail": str(exc)})
UPLOAD_PATH_SUFFIXES = ("/fulltext/upload", "/api/notes/voice", "/api/notes/photo")
# X.143. Added first, so it runs innermost: inside the session middleware, where the caller's user id is known.
rate_limit_counter = FixedWindowCounter()
app.add_middleware(RateLimitMiddleware, upload_path_suffix=UPLOAD_PATH_SUFFIXES, counter=rate_limit_counter)
app.add_middleware(
    ContentLengthLimitMiddleware,
    max_body_bytes=settings.max_request_body_bytes,
    upload_path_suffix=UPLOAD_PATH_SUFFIXES,
    upload_max_bytes=settings.object_storage_max_bytes,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    https_only=settings.environment == "production",
    same_site="lax",
    max_age=settings.session_max_age_seconds,
)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=[NEXT_CURSOR_HEADER],  # X.142: a cross-origin client must be able to read the next-page cursor
    )
app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(projects.catalogue, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(observability.router, prefix="/api")
app.include_router(accuracy.router, prefix="/api")
app.include_router(ai_settings.router, prefix="/api")
app.include_router(ai_locks.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(gates.router, prefix="/api")
app.include_router(guide.router, prefix="/api")
app.include_router(privacy.router, prefix="/api")
app.include_router(bias_warnings.router, prefix="/api")
app.include_router(stage.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(profile.catalogue, prefix="/api")
app.include_router(connectors.router, prefix="/api")
app.include_router(criteria.router, prefix="/api")
app.include_router(screening.router, prefix="/api")
app.include_router(extractions.router, prefix="/api")
app.include_router(meta_analysis.router, prefix="/api")
app.include_router(claims.router, prefix="/api")
app.include_router(extraction_schemas.router, prefix="/api")
app.include_router(clusters.router, prefix="/api")
app.include_router(coverage.router, prefix="/api")
app.include_router(design_tools.router, prefix="/api")
app.include_router(ethics_pack.router, prefix="/api")
app.include_router(preregistration.router, prefix="/api")
app.include_router(instrument.router, prefix="/api")
app.include_router(datasets.router, prefix="/api")
app.include_router(analysis_plan.router, prefix="/api")
app.include_router(qual_coding.router, prefix="/api")
app.include_router(transcripts.router, prefix="/api")
app.include_router(constructs.router, prefix="/api")
app.include_router(conceptual_model.router, prefix="/api")
app.include_router(gaps.router, prefix="/api")
app.include_router(research_questions.router, prefix="/api")
app.include_router(brief.router, prefix="/api")
app.include_router(idea_brief.router, prefix="/api")
app.include_router(evidence_matrix.router, prefix="/api")
app.include_router(bibliometrics.router, prefix="/api")
app.include_router(replication.router, prefix="/api")
app.include_router(prisma.router, prefix="/api")
app.include_router(searches.router, prefix="/api")
app.include_router(snowball.router, prefix="/api")
app.include_router(seeds.router, prefix="/api")
app.include_router(members.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(research_runs.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(integrity.router, prefix="/api")
app.include_router(provenance_reports.router, prefix="/api")
app.include_router(suggestions.router, prefix="/api")
app.include_router(workspace_ai.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(zotero.router, prefix="/api")
app.include_router(reviews.router, prefix="/api")
app.include_router(journals.router, prefix="/api")
app.include_router(submissions.router, prefix="/api")
app.include_router(scholar_metrics.router, prefix="/api")
app.include_router(interpretation.router, prefix="/api")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
    # X.4.2: production is HTTPS-only (PUBLIC_ORIGIN is validated as https), so tell browsers to stay on HTTPS.
    if settings.environment == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # FastAPI's local Swagger page loads its own third-party assets. Production disables it entirely.
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'; style-src 'self'; script-src 'self'",
        )
    # The page and its assets are unversioned, so browsers must revalidate them on every load (unchanged files
    # cost a 304); otherwise a new index.html can run against a cached old styles.css/app.js after a deploy.
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/ready")
def readiness() -> JSONResponse:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        # The probe answers only "unavailable"; the reason goes to the log (X.145). The exception text
        # names the host and database, never the password (SQLAlchemy masks it in URLs).
        logger.exception("Readiness check: database unavailable")
        return JSONResponse(status_code=503, content={"status": "not ready", "database": "unavailable"})
    return JSONResponse(content={"status": "ready", "database": "available"})


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "index.html")

@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "manifest.webmanifest", media_type="application/manifest+json")


app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "web"), name="assets")
