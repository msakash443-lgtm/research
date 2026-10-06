# ARC Retrieval Integration

## Context

`research-main` (`research-main/research-main/research/backend`) is a deliberately constrained FastAPI "evidence-first" research platform: its `app.agent.executor.execute_research_run` only synthesizes answers from manually-entered `Source`/`SourceExcerpt` rows, and its README states the boundary explicitly — no web scraping, no arbitrary retrieval, by design, until SSRF/prompt-injection/provenance controls exist.

`AutoResearchClaw-main` (`AutoResearchClaw-main/AutoResearchClaw-main`, package `researchclaw`) is a large, separate autonomous research framework (web search/scholar/crawl/PDF extraction, debate engine, idea→paper pipeline, sandboxed code execution). Its `researchclaw/web/agent.py::WebSearchAgent` already has the needed SSRF protection (`researchclaw/web/_ssrf.py::check_url_ssrf`, used inside `crawler.py` and `pdf_extractor.py`).

Goal: let a `research-main` research run optionally use ARC's `WebSearchAgent` to auto-discover sources (web search, Google Scholar, crawled pages, PDF full text) and feed them into `research-main`'s own existing evidence-grounded synthesis (`OpenAICompatibleLLM` + `_agent_prompts`), without touching ARC's paper-writing pipeline (wrong semantic fit — minutes-scale Q&A vs. hours-scale paper generation) and without changing any existing default behavior, test, or endpoint when the capability is left off (default).

Decision: isolate ARC as its own HTTP microservice (own process, own heavy dependency tree: crawl4ai, scholarly, tavily-python, PyMuPDF). `research-main` talks to it over HTTP only when a project explicitly opts in per research run. This keeps `research-main`'s process free of ARC's dependencies and keeps the "no retrieval by default" boundary intact — only a human who checks a box on a specific run grants it.

## Approach

### 1. New ARC retrieval microservice (in `AutoResearchClaw-main/AutoResearchClaw-main`)

Create `researchclaw/server/retrieval_api.py` — a standalone FastAPI app, independent of `researchclaw/server/app.py` (do **not** reuse `create_app`; that wires the dashboard/pipeline/websocket/projects stack, which is out of scope and pulls in unrelated state).

```python
"""Standalone HTTP wrapper around WebSearchAgent for external callers (e.g. research-main)."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from researchclaw.server.middleware.auth import TokenAuthMiddleware
from researchclaw.web.agent import WebSearchAgent

logger = logging.getLogger(__name__)

MAX_WEB_RESULTS = 10
MAX_SCHOLAR_RESULTS = 5
MAX_CRAWL_URLS = 5
MAX_FIELD_CHARS = 6000


class RetrieveRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=2000)
    max_web_results: int = 8
    max_scholar_results: int = 5
    max_crawl_urls: int = 5


def _truncate(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS]


def _serialize(result) -> dict:
    return {
        "topic": result.topic,
        "elapsed_seconds": result.elapsed_seconds,
        "web_results": [
            {"title": r.title, "url": r.url, "snippet": r.snippet, "content": _truncate(r.content)}
            for r in result.web_results[:20]
        ],
        "scholar_papers": [
            {
                "title": p.title, "url": p.url or None, "authors": p.authors or None,
                "year": p.year or None, "abstract": _truncate(p.abstract),
            }
            for p in result.scholar_papers[:20]
        ],
        "crawled_pages": [
            {"url": c.url, "title": c.title or None, "markdown": _truncate(c.markdown)}
            for c in result.crawled_pages if c.has_content
        ],
        "pdf_extractions": [
            {
                "url": d.path, "title": d.title or None, "authors": d.authors or None,
                "abstract": _truncate(d.abstract), "text": _truncate(d.text),
            }
            for d in result.pdf_extractions if d.success
        ],
    }


def create_retrieval_app() -> FastAPI:
    app = FastAPI(title="ResearchClaw Retrieval API", version="1.0.0", docs_url=None, redoc_url=None)
    token = os.environ.get("ARC_RETRIEVAL_TOKEN", "")
    app.add_middleware(TokenAuthMiddleware, token=token)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/retrieve")
    async def retrieve(payload: RetrieveRequest) -> dict:
        agent = WebSearchAgent(
            tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
            max_web_results=min(payload.max_web_results, MAX_WEB_RESULTS),
            max_scholar_results=min(payload.max_scholar_results, MAX_SCHOLAR_RESULTS),
            max_crawl_urls=min(payload.max_crawl_urls, MAX_CRAWL_URLS),
        )
        try:
            result = await run_in_threadpool(agent.search_and_extract, payload.topic)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Retrieval failed for topic %r", payload.topic)
            raise HTTPException(status_code=502, detail="Retrieval failed") from exc
        return _serialize(result)

    return app


app = create_retrieval_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("ARC_RETRIEVAL_HOST", "0.0.0.0"),
        port=int(os.environ.get("ARC_RETRIEVAL_PORT", "8800")),
    )


if __name__ == "__main__":
    main()
```

Notes fixed by this code (no implementer discretion left):
- `run_in_threadpool` is required: `WebSearchAgent.search_and_extract` is synchronous and can block tens of seconds (crawling/PDF download); calling it directly in an `async def` route would block the whole event loop.
- `PDFContent` has no `url` field — `extract_from_url` stores the URL in `.path` (confirmed in `researchclaw/web/pdf_extractor.py:133-153`); serialize `d.path` as `"url"`.
- `CrawlResult.has_content` and `PDFContent.success`/`.error` are the existing signal for "did we get usable text" — filter on them, do not re-derive.
- `TokenAuthMiddleware` (`researchclaw/server/middleware/auth.py`) already no-ops when `token == ""`; reuse it rather than writing new auth.
- Hard caps (`MAX_WEB_RESULTS=10` etc.) are enforced server-side via `min(...)` regardless of what the caller requests — defense in depth since this service will be reachable from another process/trust boundary.

Edit `pyproject.toml` (`AutoResearchClaw-main/AutoResearchClaw-main/pyproject.toml`):
```
[project.scripts]
researchclaw = "researchclaw.cli:main"
researchclaw-retrieval = "researchclaw.server.retrieval_api:main"
```
Add `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `httpx>=0.24` to the base `dependencies` list (lines 6-11) — `researchclaw/server/app.py` already imports `fastapi`/`starlette` without a declared dependency; this plan adds the missing declaration rather than leaving it implicit, and `retrieval_api.py` now also needs it as a hard (non-optional) dependency of this entrypoint.

Create `AutoResearchClaw-main/AutoResearchClaw-main/Dockerfile.retrieval` (new file, does not touch the existing `docker/Dockerfile*` sandbox images):
```dockerfile
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md /app/
COPY researchclaw /app/researchclaw
RUN pip install --no-cache-dir ".[web,pdf]"
EXPOSE 8800
CMD ["researchclaw-retrieval"]
```
Create `AutoResearchClaw-main/AutoResearchClaw-main/docker-compose.retrieval.yml` (new, standalone — does **not** modify `research-main`'s `docker-compose.yml`, so existing `docker compose up` there is unaffected):
```yaml
services:
  arc-retrieval:
    build:
      context: .
      dockerfile: Dockerfile.retrieval
    environment:
      TAVILY_API_KEY: ${TAVILY_API_KEY:-}
      ARC_RETRIEVAL_TOKEN: ${ARC_RETRIEVAL_TOKEN:?set a shared secret}
    ports:
      - "8800:8800"
```
Operators run this independently (`docker compose -f docker-compose.retrieval.yml up --build`) and point `research-main` at it via `ARC_RETRIEVAL_BASE_URL`.

### 2. `research-main` config: add the opt-in flags (off by default)

Edit `research-main/research-main/research/backend/app/config.py` — add fields to `Settings` (after `max_request_body_bytes`, before `model_config`):
```python
    arc_retrieval_enabled: bool = False
    arc_retrieval_base_url: str | None = None
    arc_retrieval_token: SecretStr | None = None
    arc_retrieval_timeout_seconds: float = 120
    arc_max_web_results: int = 8
    arc_max_scholar_results: int = 5
    arc_max_crawl_urls: int = 5
```
Add to `validate_production_settings` (after the existing Google OAuth check, before `return self`):
```python
        if self.arc_retrieval_enabled:
            if not self.arc_retrieval_base_url or not self.arc_retrieval_base_url.startswith("https://"):
                raise ValueError("ARC_RETRIEVAL_BASE_URL must use HTTPS in production when ARC_RETRIEVAL_ENABLED is true")
            if not self.arc_retrieval_token or len(self.arc_retrieval_token.get_secret_value()) < 16:
                raise ValueError("ARC_RETRIEVAL_TOKEN must be set in production when ARC_RETRIEVAL_ENABLED is true")
```
All new fields default to off/`None`; `get_settings()` behavior for every existing deployment and every existing test (which never sets these env vars) is unchanged.

### 3. New `app.agent.arc_client` module — normalize ARC's response, isolate the HTTP boundary

Create `research-main/research-main/research/backend/app/agent/arc_client.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings


class ArcRetrievalError(RuntimeError):
    pass


@dataclass
class ArcRetrievedSource:
    title: str
    url: str | None
    authors: list[str] | None
    year: int | None
    source_type: str
    excerpt: str | None
    locator: str | None


class ArcRetrievalClient:
    """HTTP client for the AutoResearchClaw retrieval microservice.

    The service is an external, separately-deployed system: bounded timeout,
    no retries. Callers must treat ArcRetrievalError as non-fatal to the run.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def retrieve(self, topic: str) -> list[ArcRetrievedSource]:
        base_url = self.settings.arc_retrieval_base_url
        if not base_url:
            raise ArcRetrievalError("ARC_RETRIEVAL_BASE_URL is not configured.")
        url = f"{base_url.rstrip('/')}/v1/retrieve"
        headers = {"Content-Type": "application/json"}
        if self.settings.arc_retrieval_token:
            headers["Authorization"] = f"Bearer {self.settings.arc_retrieval_token.get_secret_value()}"
        payload = {
            "topic": topic,
            "max_web_results": self.settings.arc_max_web_results,
            "max_scholar_results": self.settings.arc_max_scholar_results,
            "max_crawl_urls": self.settings.arc_max_crawl_urls,
        }
        try:
            with httpx.Client(timeout=self.settings.arc_retrieval_timeout_seconds, follow_redirects=False) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ArcRetrievalError("The ARC retrieval service could not be reached.") from exc
        except ValueError as exc:
            raise ArcRetrievalError("The ARC retrieval service returned invalid JSON.") from exc
        return _normalize(data)


def _normalize(data: dict) -> list[ArcRetrievedSource]:
    items: list[ArcRetrievedSource] = []
    for r in data.get("web_results") or []:
        if r.get("title") and r.get("url"):
            items.append(ArcRetrievedSource(
                title=r["title"], url=r["url"], authors=None, year=None,
                source_type="web_search", excerpt=r.get("content") or r.get("snippet"),
                locator="Web search result",
            ))
    for p in data.get("scholar_papers") or []:
        if p.get("title"):
            items.append(ArcRetrievedSource(
                title=p["title"], url=p.get("url") or None, authors=p.get("authors") or None,
                year=p.get("year") or None, source_type="scholar", excerpt=p.get("abstract"),
                locator="Google Scholar abstract",
            ))
    for c in data.get("crawled_pages") or []:
        if c.get("url"):
            items.append(ArcRetrievedSource(
                title=c.get("title") or c["url"], url=c["url"], authors=None, year=None,
                source_type="crawled_page", excerpt=c.get("markdown"),
                locator="Crawled page content",
            ))
    for d in data.get("pdf_extractions") or []:
        if d.get("url"):
            items.append(ArcRetrievedSource(
                title=d.get("title") or d["url"], url=d["url"], authors=d.get("authors") or None,
                year=None, source_type="pdf_extract", excerpt=d.get("abstract") or d.get("text"),
                locator="PDF full-text extraction",
            ))
    return items
```
This is the only file that knows ARC's wire format; `executor.py` only sees `ArcRetrievedSource`.

### 4. Persist the per-run opt-in (durable-run invariant)

`execute_research_run(run_id)` is called by both inline execution and `app/worker.py` with **only the run id** — the worker re-fetches the run from the DB in a separate process, so any per-request input must live on the `ResearchRun` row, not be threaded through as a function argument.

Edit `research-main/research-main/research/backend/app/models.py` — add to `ResearchRun` (after `provider_model`):
```python
    use_web_retrieval: Mapped[bool] = mapped_column(default=False)
```

Add Alembic migration `research-main/research-main/research/backend/migrations/versions/20261001_0002_add_use_web_retrieval.py`:
```python
"""Add use_web_retrieval to research_runs.

Revision ID: 20261001_0002
Revises: 20260907_0001
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261001_0002"
down_revision: Union[str, Sequence[str], None] = "20260907_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("use_web_retrieval", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("research_runs", "use_web_retrieval")
```

Edit `research-main/research-main/research/backend/app/schemas.py`:
- `ResearchRunCreate`: add `use_web_retrieval: bool = False` field.
- `ResearchRunRead`: add `use_web_retrieval: bool` field (so the UI can show it was requested).

Edit `research-main/research-main/research/backend/app/routers/research_runs.py::create_research_run` — reject the flag when the deployment has not enabled the capability, and persist it on the row:
```python
    settings = get_settings()
    if payload.use_web_retrieval and not settings.arc_retrieval_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Web retrieval is not enabled on this deployment.",
        )
    since = ...  # existing rate-limit check unchanged
    ...
    run = ResearchRun(
        project_id=project.id,
        question=payload.question.strip(),
        status=ResearchRunStatus.queued,
        use_web_retrieval=payload.use_web_retrieval,
    )
```
(Keep the existing `settings = get_settings()` call that is already first in the function; just add the new check immediately after it, before the existing daily-limit query.)

### 5. Executor: ingest ARC sources before building the evidence snapshot

Edit `research-main/research-main/research/backend/app/agent/executor.py`:
- Add imports: `import hashlib`, `import logging`, `from app.agent.arc_client import ArcRetrievalClient, ArcRetrievalError`.
- Add `logger = logging.getLogger(__name__)` and constant `MAX_ARC_SOURCES_PER_RUN = 15` near the existing `MAX_*` constants.
- Add a new function, placed after `_source_snapshot` and before `build_plan`:
```python
def _ingest_arc_sources(db, project: Project, run: ResearchRun, settings) -> None:
    try:
        retrieved = ArcRetrievalClient(settings).retrieve(run.question)
    except ArcRetrievalError as exc:
        logger.warning("ARC retrieval failed for run %s: %s", run.id, exc)
        return

    existing_urls = {
        url for url in db.scalars(
            select(Source.url).where(Source.project_id == project.id, Source.url.is_not(None))
        ).all()
    }
    added = 0
    for item in retrieved:
        if added >= MAX_ARC_SOURCES_PER_RUN:
            break
        if item.url and item.url in existing_urls:
            continue
        source = Source(
            project_id=project.id,
            title=item.title[:500],
            url=item.url,
            authors=item.authors,
            year=item.year,
            source_type=item.source_type,
            metadata_verified=False,
        )
        db.add(source)
        db.flush()
        if item.excerpt:
            content = _compact(item.excerpt, MAX_EXCERPT_CHARS_PER_SOURCE)
            db.add(SourceExcerpt(
                source_id=source.id,
                content=content,
                locator=item.locator,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            ))
        if item.url:
            existing_urls.add(item.url)
        added += 1
    if added:
        db.commit()
```
  (`content_hash` formula matches `app/routers/sources.py::create_source` exactly — same auditable-hash convention, not a new one.)
- In `execute_research_run`, insert the call right after the existing `project is None` early-return block and before `context_rows = db.scalars(...)`:
```python
        if run.use_web_retrieval and settings.arc_retrieval_enabled:
            _ingest_arc_sources(db, project, run, settings)
```
  This requires `settings = get_settings()` to be available at that point — move the existing `settings = get_settings()` line (currently inside the `else` branch near the LLM call, line 155) up to immediately after `project = db.get(...)` so it is in scope for both the new retrieval call and the existing LLM call. Do not call `get_settings()` twice.
- No other lines in `execute_research_run` change: the subsequent `sources = db.scalars(select(Source)...)` query (unchanged) will naturally pick up any newly-inserted ARC sources since it runs after ingestion, ordered by `created_at desc`, capped at the existing `MAX_SOURCES = 25`.

Behavior when `use_web_retrieval` is `False` (the default for every existing run and every existing test): `_ingest_arc_sources` is never called — `execute_research_run` is byte-for-byte the same control flow as before, aside from the relocated (not re-evaluated differently) `settings = get_settings()` line.

### 6. Frontend (minimal, optional-looking checkbox)

Edit `research-main/research-main/research/backend/app/web/index.html` and `app.js` to add a checkbox "Use automated web/scholar retrieval for this run" on the research-run creation form, sending `use_web_retrieval` in the `POST /api/projects/{id}/research-runs` body; default unchecked. Disable/hide the checkbox when `GET /api/config`-style capability isn't exposed — since no such endpoint exists today, instead: always show it, and rely on the existing 400 response (`"Web retrieval is not enabled on this deployment."`) surfaced through the existing error-rendering path in `app.js` (locate the existing fetch-error handler for research-run creation and confirm it already renders `detail` from a non-2xx JSON body before wiring the new field — if it does not, extend that handler to show `detail`, not just a generic failure message).

## Critical files & anchors

- `research-main/research-main/research/backend/app/agent/executor.py:92-184` — `execute_research_run`; insertion point for `_ingest_arc_sources` and the relocated `get_settings()` call.
- `research-main/research-main/research/backend/app/routers/research_runs.py:26-52` — `create_research_run`; add the 400 gate and persist `use_web_retrieval`.
- `research-main/research-main/research/backend/app/models.py:129-147` — `ResearchRun`; add the new column (must match the migration exactly).
- `AutoResearchClaw-main/AutoResearchClaw-main/researchclaw/web/agent.py:134-348` — `WebSearchAgent`; the exact constructor kwargs and dataclass field names (`CrawlResult.has_content`, `PDFContent.path`/`.success`) that `retrieval_api.py`'s `_serialize` must match.
- `AutoResearchClaw-main/AutoResearchClaw-main/researchclaw/server/middleware/auth.py` — `TokenAuthMiddleware`; reused as-is for the new service's auth, do not reimplement.

## Verification

1. **ARC retrieval service smoke test** (from `AutoResearchClaw-main/AutoResearchClaw-main`):
   ```
   pip install -e ".[web,pdf]" fastapi "uvicorn[standard]" httpx
   ARC_RETRIEVAL_TOKEN=test-token researchclaw-retrieval
   ```
   In another shell:
   ```
   curl -s -X POST http://127.0.0.1:8800/v1/retrieve \
     -H "Authorization: Bearer test-token" -H "Content-Type: application/json" \
     -d '{"topic":"knowledge distillation survey"}'
   ```
   Expect HTTP 200 with a JSON body containing a non-empty `web_results` array (Tavily key absent → falls back to DuckDuckGo per `researchclaw/web/search.py` docstring, still returns results).

2. **research-main unit test for the new ingestion path** — add `research-main/research-main/research/backend/tests/test_arc_retrieval.py`:
   - Monkeypatch `app.agent.executor.ArcRetrievalClient.retrieve` to return two canned `ArcRetrievedSource` items (one with a URL already present as an existing `Source`, one new).
   - Set `settings.arc_retrieval_enabled = True` (via `app.config.get_settings` override / env vars in the test, following the existing `conftest.py` pattern of setting env vars before import).
   - Create a project, create a `ResearchRun` with `use_web_retrieval=True` directly via `execute_research_run`.
   - Assert: exactly one new `Source` row is inserted (the duplicate URL is skipped), its `metadata_verified is False`, its `source_type` matches the canned item, and a `SourceExcerpt` with the correct `content_hash` (`hashlib.sha256(content.encode()).hexdigest()`) was created.
   - Assert: `POST /api/projects/{id}/research-runs` with `use_web_retrieval=true` returns 400 when `arc_retrieval_enabled` is `False`.

3. **Full existing suite unaffected**: `cd research-main/research-main/research/backend && pytest -q` — must pass unmodified (proves the default-off path is behaviorally identical to before this change).

4. **End-to-end manual smoke** with both services running: start the ARC retrieval service (step 1), then start research-main with `ARC_RETRIEVAL_ENABLED=true ARC_RETRIEVAL_BASE_URL=http://127.0.0.1:8800 ARC_RETRIEVAL_TOKEN=test-token RUN_RESEARCH_INLINE=true uvicorn app.main:app --reload --app-dir backend`. Create a project, `POST` a research run with `use_web_retrieval: true`, then `GET /api/projects/{id}/sources` and confirm new rows exist with `source_type` in `{web_search, scholar, crawled_page, pdf_extract}` and `metadata_verified: false`, and `GET` the research run shows `status: completed` (or `needs_configuration` if no `LLM_*` is set) with an `answer` citing `[S1]`-style markers.

## Assumptions & contingencies

- **ARC retrieval service deployment topology**: assumed to run as an independently deployed process/container (no shared `docker-compose.yml` with research-main), reachable over HTTP at an operator-supplied `ARC_RETRIEVAL_BASE_URL`. If the real requirement is "always co-located," add the service to `research-main`'s existing `docker-compose.yml` under a `profiles: ["arc"]` entry referencing a build context that includes the sibling `AutoResearchClaw-main` checkout — deferred because it requires fixing a relative path between two independently-cloned repos, which is environment-specific.
- **Web search provider**: assumed Tavily is optional (`TAVILY_API_KEY` unset → DuckDuckGo fallback, per `researchclaw/web/search.py`). If Tavily is required in practice, set `TAVILY_API_KEY` on the ARC retrieval service's environment only — research-main never sees that key.
- **Dedup key**: sources are deduped by exact `url` match within the project. If ARC returns the same paper from both Scholar and a web search with different URLs, both are kept (no cross-source title/DOI dedup) — acceptable since `metadata_verified=False` already signals "needs human review," and an audited de-dup heuristic (fuzzy title match) is out of scope unless requested.
