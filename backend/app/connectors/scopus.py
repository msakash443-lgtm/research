"""Scopus Search API connector (M1.4.7).

Uses the official Elsevier Search API only. Search offsets are capped at 5,000 and
the API's maximum page size is 200. The API key is sent in a header, not a URL.
Reference: https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.doi import normalize_doi

BASE_URL = "https://api.elsevier.com/content"
NAME = "scopus"
MAX_PAGE = 200
MAX_OFFSET = 5000
SUPPORTED_FILTERS = ("year_from", "year_to")
_ID = re.compile(r"^(?:2-s2\.0-)?\d{1,20}$")
_OFFSET = re.compile(r"^\d{1,4}$")
_YEAR = re.compile(r"^\d{4}$")


def _text(value: Any, limit: int) -> str | None:
    return " ".join(value.split())[:limit] or None if isinstance(value, str) and value.strip() else None


def to_record(entry: Any) -> PaperRecord | None:
    if not isinstance(entry, dict):
        return None
    title = _text(entry.get("dc:title"), 500)
    raw_id = _text(entry.get("dc:identifier"), 255)
    if raw_id and raw_id.startswith("SCOPUS_ID:"):
        raw_id = raw_id.removeprefix("SCOPUS_ID:")
    if not title or not raw_id or not _ID.fullmatch(raw_id):
        return None
    ident = raw_id.removeprefix("2-s2.0-")
    flags: list[str] = []
    try:
        doi = normalize_doi(entry.get("prism:doi") if isinstance(entry.get("prism:doi"), str) else None)
    except ValueError:
        doi = None
        flags.append("invalid_doi")
    author = _text(entry.get("dc:creator"), 200)
    date = _text(entry.get("prism:coverDate"), 10)
    year = int(date[:4]) if date and date[:4].isdigit() and 1000 <= int(date[:4]) <= 2200 else None
    abstract = _text(entry.get("dc:description"), 20000)
    if not abstract:
        flags.append("no_abstract")
    return PaperRecord(
        connector=NAME,
        external_id=ident,
        title=title,
        doi=doi,
        authors=(author,) if author else (),
        year=year,
        venue=_text(entry.get("prism:publicationName"), 500),
        abstract=abstract,
        url=f"https://www.scopus.com/record/display.uri?eid=2-s2.0-{ident}",
        source_ids={NAME: ident},
        quality_flags=tuple(flags),
    )


class ScopusConnector(ConnectorBase):
    name = NAME

    def __init__(
        self,
        http: ConnectorHttpClient | None = None,
        api_key: SecretStr | None = None,
        inst_token: SecretStr | None = None,
    ):
        key = api_key.get_secret_value().strip() if api_key else ""
        if http is None:
            if not key:
                raise ConnectorError("Scopus needs SCOPUS_API_KEY; there is no anonymous access")
            headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
            token = inst_token.get_secret_value().strip() if inst_token else ""
            if token:
                headers["X-ELS-Insttoken"] = token
            http = ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=1.0), headers=headers)
        self._http = http
        self.skipped_records = 0
        self.last_search_capped = False

    def _records(self, entries: Any) -> list[PaperRecord]:
        if not isinstance(entries, list):
            raise ConnectorError("Scopus returned an unexpected reply")
        records = []
        for entry in entries:
            record = to_record(entry)
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _query(query: str, filters: dict[str, Any]) -> str:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported Scopus filter(s): " + ", ".join(unknown))
        parts = [f"({query})" if filters else query]
        years = {}
        for key, op in (("year_from", ">="), ("year_to", "<=")):
            if key in filters:
                value = filters[key]
                if isinstance(value, bool) or not _YEAR.fullmatch(str(value)) or not 1000 <= int(value) <= 2200:
                    raise ConnectorError(f"{key} must be a four-digit year")
                years[key] = int(value)
                if key == "year_from":
                    parts.append(f"PUBYEAR > {years[key] - 1}")
                else:
                    parts.append(f"PUBYEAR < {years[key] + 1}")
        if years.get("year_from", 0) > years.get("year_to", 9999):
            raise ConnectorError("year_from cannot be later than year_to")
        return " AND ".join(parts)

    @staticmethod
    def _id(value: str) -> str:
        text = (value or "").strip()
        if text.startswith("SCOPUS_ID:"):
            text = text.removeprefix("SCOPUS_ID:")
        if not _ID.fullmatch(text):
            raise ConnectorError("A Scopus id must be numeric, optionally prefixed with 2-s2.0-")
        return text.removeprefix("2-s2.0-")

    def search(self, request: SearchRequest) -> SearchPage:
        offset = 0
        if request.cursor is not None:
            if not _OFFSET.fullmatch(request.cursor):
                raise ConnectorError("Invalid cursor for Scopus")
            offset = int(request.cursor)
        if offset >= MAX_OFFSET:
            raise ConnectorError("Scopus search cannot page past the first 5,000 results")
        count = min(request.limit, MAX_PAGE, MAX_OFFSET - offset)
        data = self._http.get_json(
            "/search/scopus",
            {"query": self._query(request.query, request.filters), "count": count, "start": offset, "view": "STANDARD"},
        )
        result = data.get("search-results") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            raise ConnectorError("Scopus returned an unexpected reply")
        entries = result.get("entry", [])
        records = self._records(entries)
        try:
            total = int(result.get("opensearch:totalResults"))
            if total < 0:
                total = None
        except (TypeError, ValueError):
            total = None
        nxt = offset + len(entries)
        more = bool(entries) and (total is None or nxt < total)
        self.last_search_capped = more and nxt >= MAX_OFFSET
        return SearchPage(records=tuple(records), total=total, next_cursor=str(nxt) if more and nxt < MAX_OFFSET else None)

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        ident = self._id(external_id)
        page = self.search(SearchRequest(query=f"SCOPUS_ID({ident})", limit=1))
        return next((record for record in page.records if record.external_id == ident), None)
