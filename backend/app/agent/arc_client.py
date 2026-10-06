from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.doi import normalize_doi


# Every `source_type` built below must be in `models.ARC_SOURCE_TYPES`, or the source
# is treated as researcher-entered (tests/test_arc_retrieval.py checks this).


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
    doi: str | None = None


class ArcRetrievalClient:
    """HTTP client for the AutoResearchClaw retrieval microservice.

    The service is an external, separately deployed system: bounded timeout,
    no retries. Callers must treat ArcRetrievalError as non-fatal to the run.
    This is the only module that knows ARC's wire format.
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
        except httpx.HTTPStatusError as exc:
            raise ArcRetrievalError(f"The ARC retrieval service returned HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise ArcRetrievalError("The ARC retrieval service could not be reached.") from exc
        except ValueError as exc:
            raise ArcRetrievalError("The ARC retrieval service returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ArcRetrievalError("The ARC retrieval service returned an unexpected response.")
        return _normalize(data)


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _http_url(value: Any) -> str | None:
    text = _text(value)
    return text[:2000] if text and text.lower().startswith(("http://", "https://")) else None


def _authors(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names = [name.strip() for name in value if isinstance(name, str) and name.strip()]
    return names or None


def _doi(value: Any) -> str | None:
    try:
        return normalize_doi(value) if isinstance(value, str) else None
    except ValueError:
        return None  # a malformed DOI from the service is dropped, never stored


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and 1000 <= value <= 3000 else None


def _items(data: dict, key: str) -> list[dict]:
    value = data.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _normalize(data: dict) -> list[ArcRetrievedSource]:
    items: list[ArcRetrievedSource] = []
    for r in _items(data, "web_results"):
        title, url = _text(r.get("title")), _http_url(r.get("url"))
        if title and url:
            items.append(ArcRetrievedSource(
                title=title, url=url, authors=None, year=None, source_type="web_search",
                excerpt=_text(r.get("content")) or _text(r.get("snippet")), locator="Web search result",
            ))
    for p in _items(data, "scholar_papers"):
        title = _text(p.get("title"))
        if title:
            items.append(ArcRetrievedSource(
                title=title, url=_http_url(p.get("url")), authors=_authors(p.get("authors")),
                year=_year(p.get("year")), source_type="scholar", excerpt=_text(p.get("abstract")),
                locator="Google Scholar abstract", doi=_doi(p.get("doi")),
            ))
    for c in _items(data, "crawled_pages"):
        url = _http_url(c.get("url"))
        if url:
            items.append(ArcRetrievedSource(
                title=_text(c.get("title")) or url, url=url, authors=None, year=None,
                source_type="crawled_page", excerpt=_text(c.get("markdown")), locator="Crawled page content",
            ))
    for d in _items(data, "pdf_extractions"):
        url = _http_url(d.get("url"))
        if url:
            items.append(ArcRetrievedSource(
                title=_text(d.get("title")) or url, url=url, authors=_authors(d.get("authors")), year=None,
                source_type="pdf_extract", excerpt=_text(d.get("abstract")) or _text(d.get("text")),
                locator="PDF full-text extraction", doi=_doi(d.get("doi")),
            ))
    return items
