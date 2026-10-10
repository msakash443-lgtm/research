"""IEEE Xplore Metadata Search API connector (M1.4.7).

Uses the official API, its API-key query parameter, Boolean `querytext`, and
one-based record offsets. The API permits at most 200 records per page; this
connector conservatively caps retrieval at 10,000 results.
Reference: https://developer.ieee.org/docs/read/Metadata_API_details
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.doi import normalize_doi

BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search"
NAME = "ieee_xplore"
MAX_PAGE = 200
MAX_OFFSET = 10000
SUPPORTED_FILTERS = ("year_from", "year_to")
_ID = re.compile(r"^\d{1,20}$")
_OFFSET = re.compile(r"^\d{1,5}$")
_YEAR = re.compile(r"^\d{4}$")


def _text(value: Any, limit: int) -> str | None:
    return " ".join(value.split())[:limit] or None if isinstance(value, str) and value.strip() else None


def _year(value: Any) -> int | None:
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and re.fullmatch(r"\d{4}", value)
    ):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 2200 else None


def to_record(article: Any) -> PaperRecord | None:
    if not isinstance(article, dict):
        return None
    ident = _text(article.get("article_number"), 255)
    title = _text(article.get("title"), 500)
    if not ident or not _ID.fullmatch(ident) or not title:
        return None
    authors_data = article.get("authors") or []
    if isinstance(authors_data, dict):
        authors_data = authors_data.get("authors") or []
    if not isinstance(authors_data, list):
        authors_data = []
    authors = tuple(
        name
        for name in (
            _text(author.get("full_name") or author.get("name"), 200)
            for author in authors_data[:200]
            if isinstance(author, dict)
        )
        if name
    )
    flags: list[str] = []
    raw_doi = article.get("doi")
    try:
        doi = normalize_doi(raw_doi if isinstance(raw_doi, str) else None)
    except ValueError:
        doi = None
        flags.append("invalid_doi")
    abstract = _text(article.get("abstract"), 20000)
    if not abstract:
        flags.append("no_abstract")
    return PaperRecord(
        connector=NAME,
        external_id=ident,
        title=title,
        doi=doi,
        authors=authors,
        year=_year(article.get("publication_year")),
        venue=_text(article.get("publication_title"), 500),
        abstract=abstract,
        url=_text(article.get("html_url") or article.get("pdf_url"), 2000),
        source_ids={NAME: ident},
        quality_flags=tuple(flags),
    )


class IeeeXploreConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, api_key: SecretStr | None = None):
        key = api_key.get_secret_value().strip() if api_key else ""
        if http is None:
            if not key:
                raise ConnectorError("IEEE Xplore needs IEEE_XPLORE_API_KEY; there is no anonymous access")
            self._api_key = key
            http = ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=1.0))
        else:
            self._api_key = key
        self._http = http
        self.skipped_records = 0
        self.last_search_capped = False

    def _records(self, articles: Any) -> list[PaperRecord]:
        if not isinstance(articles, list):
            raise ConnectorError("IEEE Xplore returned an unexpected reply")
        records = []
        for article in articles:
            record = to_record(article)
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _filters(filters: dict[str, Any]) -> dict[str, int]:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported IEEE Xplore filter(s): " + ", ".join(unknown))
        result = {}
        for key, param in (("year_from", "start_year"), ("year_to", "end_year")):
            if key in filters:
                value = filters[key]
                if isinstance(value, bool) or not _YEAR.fullmatch(str(value)) or not 1000 <= int(value) <= 2200:
                    raise ConnectorError(f"{key} must be a four-digit year")
                result[param] = int(value)
        if "start_year" in result and "end_year" in result and result["start_year"] > result["end_year"]:
            raise ConnectorError("year_from cannot be later than year_to")
        return result

    def _search(
        self, query: str, limit: int, offset: int, filters: dict[str, Any], article_number: str | None = None
    ) -> SearchPage:
        params: dict[str, Any] = {
            "apikey": self._api_key,
            "format": "json",
            "max_records": min(limit, MAX_PAGE, MAX_OFFSET - offset),
            "start_record": offset + 1,
        }
        if query:
            params["querytext"] = query
        params.update(self._filters(filters))
        if article_number is not None:
            params["article_number"] = article_number
        data = self._http.get_json("/articles", params)
        if not isinstance(data, dict):
            raise ConnectorError("IEEE Xplore returned an unexpected reply")
        articles = data.get("articles")
        records = self._records(articles)
        try:
            total = int(data.get("total_records"))
            if total < 0:
                total = None
        except (TypeError, ValueError):
            total = None
        nxt = offset + len(articles)
        more = bool(articles) and (total is None or nxt < total)
        self.last_search_capped = more and nxt >= MAX_OFFSET
        return SearchPage(records=tuple(records), total=total, next_cursor=str(nxt) if more and nxt < MAX_OFFSET else None)

    def search(self, request: SearchRequest) -> SearchPage:
        offset = 0
        if request.cursor is not None:
            if not _OFFSET.fullmatch(request.cursor):
                raise ConnectorError("Invalid cursor for IEEE Xplore")
            offset = int(request.cursor)
        if offset >= MAX_OFFSET:
            raise ConnectorError("IEEE Xplore search cannot page past the first 10,000 results")
        return self._search(request.query, request.limit, offset, request.filters)

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        ident = (external_id or "").strip()
        if not _ID.fullmatch(ident):
            raise ConnectorError("An IEEE Xplore id must be a numeric article number")
        page = self._search("", 1, 0, {}, article_number=ident)
        return next((record for record in page.records if record.external_id == ident), None)
