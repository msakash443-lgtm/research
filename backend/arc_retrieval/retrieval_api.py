"""Standalone HTTP wrapper around WebSearchAgent for external callers (e.g. research-main).

Independent of the ARC dashboard app: no dashboard, pipeline, or websocket
state. Run with ``python -m arc_retrieval.retrieval_api``.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from arc_retrieval.middleware.auth import TokenAuthMiddleware
from arc_retrieval.web.agent import WebSearchAgent

logger = logging.getLogger(__name__)

# Hard caps enforced server-side regardless of what the caller requests.
MAX_WEB_RESULTS = 10
MAX_SCHOLAR_RESULTS = 5
MAX_CRAWL_URLS = 5
MAX_ITEMS_PER_KIND = 20
MAX_FIELD_CHARS = 6000
MIN_TOKEN_CHARS = 16


class RetrieveRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=2000)
    max_web_results: int = Field(default=8, ge=0)
    max_scholar_results: int = Field(default=5, ge=0)
    max_crawl_urls: int = Field(default=5, ge=0)


def _truncate(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS]


# DOIs are only taken from places that identify the paper itself — never from body text, where the
# first DOI-shaped string is often a cited reference. A wrong DOI is worse than none: research-main
# de-duplicates sources by DOI. research-main still validates and normalises what it receives.
_DOI = r"10\.\d{4,9}/[^\s?#]+"
_DOI_HOSTS = {"doi.org", "dx.doi.org", "www.doi.org"}
_DOI_IN_PATH = (
    re.compile(rf"/doi/(?:abs/|full/|pdf/|epdf/|pdfdirect/|reader/|book/)?({_DOI})", re.IGNORECASE),  # Wiley, T&F, SAGE, ACM
    re.compile(rf"^/(?:article|chapter|book|referenceworkentry)/({_DOI})", re.IGNORECASE),  # Springer
    re.compile(rf"^/content/pdf/({_DOI})\.pdf$", re.IGNORECASE),  # Springer PDF links
)
_DOI_ANYWHERE = re.compile(rf"(?<![\w.]){_DOI}", re.IGNORECASE)
# Page names publishers append after the DOI in a URL; not part of the DOI.
_TRAILING_PAGE = re.compile(r"/(?:full|abstract|abs|pdf|epdf|fulltext\.html|references|figures|metrics|citedby)/?$", re.IGNORECASE)
_MAX_DOI_CHARS = 255


def _clean_doi(text: str) -> str | None:
    doi = text.strip().rstrip(".,;:)]}'\"").lower()
    if len(doi) > _MAX_DOI_CHARS or not re.fullmatch(_DOI, doi, re.IGNORECASE):
        return None
    return doi


def _doi_from_url(url: str | None) -> str | None:
    """The DOI a paper URL encodes: a doi.org link or a publisher path that embeds it. Else None."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    path = unquote(parts.path)
    if (parts.hostname or "").lower() in _DOI_HOSTS:
        return _clean_doi(path.lstrip("/"))
    for pattern in _DOI_IN_PATH:
        match = pattern.search(path)
        if match:
            return _clean_doi(_TRAILING_PAGE.sub("", match.group(1)))
    return None


def _doi_from_pdf_metadata(metadata: dict | None) -> str | None:
    """A DOI from the PDF's own metadata: an explicit `doi` key, or a subject/keywords field holding exactly one."""
    if not isinstance(metadata, dict):
        return None
    for key, value in metadata.items():
        if isinstance(key, str) and key.lower() == "doi" and isinstance(value, str):
            found = {_clean_doi(m) for m in _DOI_ANYWHERE.findall(value)} - {None}
            if len(found) == 1:
                return found.pop()
    for key in ("subject", "keywords"):
        value = metadata.get(key)
        if isinstance(value, str):
            found = {_clean_doi(m) for m in _DOI_ANYWHERE.findall(value)} - {None}
            if len(found) == 1:
                return found.pop()
    return None


def _serialize(result) -> dict:
    return {
        "topic": result.topic,
        "elapsed_seconds": result.elapsed_seconds,
        "web_results": [
            {"title": r.title, "url": r.url, "snippet": r.snippet, "content": _truncate(r.content)}
            for r in result.web_results[:MAX_ITEMS_PER_KIND]
        ],
        "scholar_papers": [
            {
                "title": p.title,
                "url": p.url or None,
                "authors": p.authors or None,
                "year": p.year or None,
                "abstract": _truncate(p.abstract),
                "doi": _doi_from_url(p.url),
            }
            for p in result.scholar_papers[:MAX_ITEMS_PER_KIND]
        ],
        "crawled_pages": [
            {"url": c.url, "title": c.title or None, "markdown": _truncate(c.markdown)}
            for c in result.crawled_pages[:MAX_ITEMS_PER_KIND]
            if c.has_content
        ],
        # PDFContent stores the source URL in ``path`` (see PDFExtractor.extract_from_url).
        "pdf_extractions": [
            {
                "url": d.path,
                "title": d.title or None,
                "authors": d.authors or None,
                "abstract": _truncate(d.abstract),
                "text": _truncate(d.text),
                "doi": _doi_from_pdf_metadata(getattr(d, "metadata", None)) or _doi_from_url(d.path),
            }
            for d in result.pdf_extractions[:MAX_ITEMS_PER_KIND]
            if d.success
        ],
    }


def create_retrieval_app(token: str | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get("ARC_RETRIEVAL_TOKEN", "")
    # TokenAuthMiddleware allows everything when the token is empty. This service
    # fetches arbitrary web content on request, so it must never run open.
    if len(token) < MIN_TOKEN_CHARS:
        raise RuntimeError(f"ARC_RETRIEVAL_TOKEN must be set to at least {MIN_TOKEN_CHARS} characters")

    app = FastAPI(title="ResearchClaw Retrieval API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
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
            # search_and_extract is synchronous and can block for tens of seconds.
            result = await run_in_threadpool(agent.search_and_extract, payload.topic)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Retrieval failed for topic %r", payload.topic)
            raise HTTPException(status_code=502, detail="Retrieval failed") from exc
        return _serialize(result)

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        create_retrieval_app(),
        host=os.environ.get("ARC_RETRIEVAL_HOST", "0.0.0.0"),
        port=int(os.environ.get("ARC_RETRIEVAL_PORT", "8800")),
    )


if __name__ == "__main__":
    main()
