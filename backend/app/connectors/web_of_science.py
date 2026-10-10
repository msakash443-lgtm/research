"""Clarivate Web of Science Starter API connector (M1.4.7).

Uses the licensed Starter API's documents endpoint, X-ApiKey authentication, topic
queries, and page-number pagination. API results are limited to 50 records per page;
the connector conservatively caps retrieval at 5,000 results.
Reference: https://developer.clarivate.com/apis/wos-starter
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy, NotFoundError
from app.doi import normalize_doi

BASE_URL = "https://api.clarivate.com"
PATH = "/apis/wos-starter/v1/documents"
NAME = "web_of_science"
MAX_PAGE = 50
MAX_PAGES = 100
SUPPORTED_FILTERS = ("year_from", "year_to")
_UID = re.compile(r"^WOS:[A-Za-z0-9-]{1,100}$")
_PAGE = re.compile(r"^\d{1,3}$")
_YEAR = re.compile(r"^\d{4}$")


def _text(value: Any, limit: int) -> str | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("displayName") or value.get("name")
    return " ".join(value.split())[:limit] or None if isinstance(value, str) and value.strip() else None


def _year(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("year")
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if 1000 <= result <= 2200 else None


def to_record(hit: Any) -> PaperRecord | None:
    if not isinstance(hit, dict):
        return None
    uid = _text(hit.get("uid") or hit.get("UID"), 255)
    title = _text(hit.get("title") or hit.get("titles"), 500)
    if not uid or not _UID.fullmatch(uid) or not title:
        return None
    identifiers = hit.get("identifiers") or {}
    identifiers = identifiers if isinstance(identifiers, dict) else {}
    source = hit.get("source") or {}
    source = source if isinstance(source, dict) else {}
    names = hit.get("names") or {}
    names = names if isinstance(names, dict) else {}
    author_list = names.get("authors") or hit.get("authors") or []
    if isinstance(author_list, dict):
        author_list = author_list.get("author") or author_list.get("names") or []
    if not isinstance(author_list, list):
        author_list = []
    authors = tuple(
        name for name in (_text(author, 200) for author in author_list[:200]) if name
    )
    flags: list[str] = []
    raw_doi = identifiers.get("doi") or hit.get("doi")
    try:
        doi = normalize_doi(raw_doi if isinstance(raw_doi, str) else None)
    except ValueError:
        doi = None
        flags.append("invalid_doi")
    abstract = _text(hit.get("abstract"), 20000)
    if not abstract:
        flags.append("no_abstract")
    return PaperRecord(
        connector=NAME,
        external_id=uid,
        title=title,
        doi=doi,
        authors=authors,
        year=_year(source.get("publishYear") or hit.get("publishYear") or hit.get("year")),
        venue=_text(source.get("sourceTitle") or source.get("title"), 500),
        abstract=abstract,
        url=f"https://www.webofscience.com/wos/woscc/full-record/{uid}",
        source_ids={NAME: uid},
        quality_flags=tuple(flags),
    )


class WebOfScienceConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, api_key: SecretStr | None = None):
        key = api_key.get_secret_value().strip() if api_key else ""
        if http is None:
            if not key:
                raise ConnectorError("Web of Science needs WEB_OF_SCIENCE_API_KEY; there is no anonymous access")
            http = ConnectorHttpClient(
                NAME,
                BASE_URL,
                HttpPolicy(min_interval_seconds=1.0),
                headers={"X-ApiKey": key, "Accept": "application/json"},
            )
        self._http = http
        self.skipped_records = 0
        self.last_search_capped = False

    def _records(self, hits: Any) -> list[PaperRecord]:
        if not isinstance(hits, list):
            raise ConnectorError("Web of Science returned an unexpected reply")
        records = []
        for hit in hits:
            record = to_record(hit)
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _query(query: str, filters: dict[str, Any]) -> str:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported Web of Science filter(s): " + ", ".join(unknown))
        parts = [f"({query})" if filters else query]
        years = {}
        for key, op in (("year_from", ">="), ("year_to", "<=")):
            if key in filters:
                value = filters[key]
                if isinstance(value, bool) or not _YEAR.fullmatch(str(value)) or not 1000 <= int(value) <= 2200:
                    raise ConnectorError(f"{key} must be a four-digit year")
                years[key] = int(value)
                parts.append(f"PY{op}{years[key]}")
        if years.get("year_from", 0) > years.get("year_to", 9999):
            raise ConnectorError("year_from cannot be later than year_to")
        return " AND ".join(parts)

    @staticmethod
    def _uid(value: str) -> str:
        text = (value or "").strip()
        if not _UID.fullmatch(text):
            raise ConnectorError("A Web of Science identifier must be a WOS: record id")
        return text

    def search(self, request: SearchRequest) -> SearchPage:
        page = 1
        if request.cursor is not None:
            if not _PAGE.fullmatch(request.cursor):
                raise ConnectorError("Invalid cursor for Web of Science")
            page = int(request.cursor)
        if not 1 <= page <= MAX_PAGES:
            raise ConnectorError("Web of Science search cannot page past the first 5,000 results")
        limit = min(request.limit, MAX_PAGE)
        data = self._http.get_json(
            PATH,
            {"db": "WOS", "q": self._query(request.query, request.filters), "limit": limit, "page": page},
        )
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise ConnectorError("Web of Science returned an unexpected reply")
        hits = data["hits"]
        records = self._records(hits)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        try:
            total = int(metadata.get("total") or metadata.get("totalCount") or metadata.get("totalResults"))
            if total < 0:
                total = None
        except (TypeError, ValueError):
            total = None
        more = bool(hits) and (total is None or page * limit < total)
        self.last_search_capped = more and page >= MAX_PAGES
        return SearchPage(records=tuple(records), total=total, next_cursor=str(page + 1) if more and page < MAX_PAGES else None)

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        uid = self._uid(external_id)
        try:
            hit = self._http.get_json(f"{PATH}/{uid}")
        except NotFoundError:
            return None
        record = to_record(hit)
        if record is None:
            raise ConnectorError("Web of Science returned a document that cannot be represented")
        return record
