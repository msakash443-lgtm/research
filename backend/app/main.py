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
from app.config import get_settings
from app.middleware import ContentLengthLimitMiddleware
from app.routers import analysis, auth, claims, literature_review, projects, research_runs, sources

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Local convenience only. Production releases must apply Alembic migrations first.
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield


settings = get_settings()
if settings.environment == "production" and settings.session_secret.get_secret_value() == "development-only-change-me":
    raise RuntimeError("SESSION_SECRET must be configured in production")

app = FastAPI(
    title="Research AI Platform",
    version="0.2.0",
    lifespan=lifespan,
    # API docs are helpful locally but should not expose an exploration surface on the public deployment.
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
)
app.add_middleware(ContentLengthLimitMiddleware, max_body_bytes=settings.max_request_body_bytes)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    https_only=settings.environment == "production",
    same_site="lax",
)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(claims.router, prefix="/api")
app.include_router(research_runs.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(literature_review.router, prefix="/api")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # FastAPI's local Swagger page loads its own third-party assets. Production disables it entirely.
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'; style-src 'self'; script-src 'self'",
        )
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
        return JSONResponse(status_code=503, content={"status": "not ready", "database": "unavailable"})
    return JSONResponse(content={"status": "ready", "database": "available"})


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "index.html")


app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "web"), name="assets")
