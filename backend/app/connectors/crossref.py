"""Crossref connector (official REST API, https://api.crossref.org).

Used for bibliographic search and for DOI metadata, e.g. to check that a cited work exists
and what its title/authors/year really are (the Citation Verifier, spec section 5). `external_id`
is the bare lower-case DOI.

Not offered by Crossref, so they raise `NotSupportedError`: cited-by lists (it only publishes a
count) and full text.

* References: `get_references` resolves the DOI-bearing entries of a work's reference list in
  batches. Entries without a DOI cannot be resolved; their number is left in
  `last_unresolved_references` so "resolved 12 of 40" is knowable, not hidden.
* Abstracts come as JATS XML; tags are stripped. The text is untrusted third-party data.
* Polite pool: an optional contact email from configuration is sent as `mailto`. If none is
  configured the public pool is used; there is no placeholder address.
* Unknown or malformed search filters raise before any request.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

from app.connectors.base import (
    ConnectorBase,
    ConnectorError,
    FullText,
    NotSupportedError,
    PaperRecord,
    SearchPage,
    SearchRequest,
)
from app.connectors.http import ConnectorHttpClient, HttpPolicy, NotFoundError
from app.doi import normalize_doi

BASE_URL = "https://api.crossref.org"
NAME = "crossref"
BATCH = 50
MAX_AUTHORS = 200
SUPPORTED_FILTERS = ("year_from", "year_to", "type")
_YEAR = re.compile(r"^\d{4}$")
_TYPE = re.compile(r"^[a-z][a-z-]{0,40}$")
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def doi_id(value: str) -> str:
    try:
        doi = normalize_doi(value)
    except ValueError as exc:
        raise ConnectorError("Crossref id must be a DOI such as 10.1234/abc") from exc
    if not doi:
        raise ConnectorError("Crossref id must be a DOI such as 10.1234/abc")
    return doi


def _str(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _first(value: Any, limit: int) -> str | None:
    return _str(value[0], limit) if isinstance(value, list) and value else None


def _http(value: Any) -> str | None:
    text = _str(value, 2000)
    return text if text and text.lower().startswith(("http://", "https://")) else None


def _abstract(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", value))).strip()
    if text.lower().startswith("abstract "):
        text = text[9:].strip()
    return text[:20000] or None


def _year(item: dict[str, Any]) -> int | None:
    for key in ("issued", "published", "published-print", "published-online", "created"):
        parts = (item.get(key) or {}).get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]
            if isinstance(year, int) and 1000 <= year <= 2200:
                return year
    return None


def _author(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    name = _str(entry.get("name"), 200)
    if name:
        return name
    given, family = _str(entry.get("given"), 100), _str(entry.get("family"), 100)
    return " ".join(part for part in (given, family) if part) or None


def to_record(item: dict[str, Any]) -> PaperRecord | None:
    """Normalise one Crossref work; None when it has no usable DOI or title."""
    try:
        doi = doi_id(str(item.get("DOI", "")))
    except ConnectorError:
        return None
    title = _first(item.get("title"), 500)
    if not title:
        return None
    abstract = _abstract(item.get("abstract"))
    flags = []
    if item.get("type") == "posted-content" or item.get("subtype") == "preprint":
        flags.append("preprint")
    if not abstract:
        flags.append("no_abstract")
    return PaperRecord(
        connector=NAME,
        external_id=doi,
        title=title,
        doi=doi,
        authors=tuple(a for e in (item.get("author") or [])[:MAX_AUTHORS] if (a := _author(e))),
        year=_year(item),
        venue=_first(item.get("container-title"), 500),
        abstract=abstract,
        url=_http(item.get("URL")),
        oa_url=None,  # Crossref has no reliable open-access location; Unpaywall (M1.4.5) does
        source_ids={NAME: doi},
        quality_flags=tuple(flags),
    )


class CrossrefConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, contact_email: str | None = None, max_related: int = 200):
        if max_related < 1:
            raise ValueError("max_related must be at least 1")
        self._http = http or ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=0.1))
        self._mailto = contact_email.strip() if contact_email and contact_email.strip() else None
        self.max_related = max_related
        self.skipped_records = 0
        self.last_related_truncated = False
        self.last_unresolved_references = 0

    def _params(self, **params: Any) -> dict[str, Any]:
        if self._mailto:
            params["mailto"] = self._mailto
        return params

    def _message(self, data: Any) -> dict[str, Any]:
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict) or data.get("status") not in (None, "ok"):
            raise ConnectorError("Crossref returned an unexpected reply")
        return message

    def _records(self, items: Any) -> list[PaperRecord]:
        if not isinstance(items, list):
            raise ConnectorError("Crossref returned an unexpected reply")
        records = []
        for item in items:
            record = to_record(item) if isinstance(item, dict) else None
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _filter(filters: dict[str, Any]) -> str:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported Crossref filter(s): " + ", ".join(unknown))
        parts = []
        for key, value in filters.items():
            if key in ("year_from", "year_to"):
                if isinstance(value, bool) or not _YEAR.match(str(value)):
                    raise ConnectorError(f"{key} must be a four-digit year")
                parts.append(f"{'from' if key == 'year_from' else 'until'}-pub-date:{value}")
            else:
                if not isinstance(value, str) or not _TYPE.match(value):
                    raise ConnectorError("type must be a lower-case Crossref type such as 'journal-article'")
                parts.append(f"type:{value}")
        return ",".join(parts)

    def search(self, request: SearchRequest) -> SearchPage:
        params = self._params(query=request.query, rows=request.limit, cursor=request.cursor or "*")
        filter_ = self._filter(request.filters)
        if filter_:
            params["filter"] = filter_
        message = self._message(self._http.get_json("/works", params))
        items = message.get("items")
        records = self._records(items)
        total = message.get("total-results")
        cursor = message.get("next-cursor")
        # Crossref keeps returning a cursor after the last page; an empty page is the end.
        more = bool(items) and isinstance(cursor, str) and bool(cursor)
        return SearchPage(
            records=tuple(records),
            total=total if isinstance(total, int) and total >= 0 else None,
            next_cursor=cursor if more else None,
        )

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        """Metadata for a DOI, or None if Crossref has no such DOI (it may be registered elsewhere)."""
        doi = doi_id(external_id)
        try:
            data = self._http.get_json("/works/" + quote(doi, safe="/()-._:;"), self._params())
        except NotFoundError:
            return None
        record = to_record(self._message(data))
        if record is None:
            raise ConnectorError("Crossref returned a work that cannot be represented (no title)")
        return record

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        doi = doi_id(external_id)
        try:
            data = self._http.get_json("/works/" + quote(doi, safe="/()-._:;"), self._params())
        except NotFoundError:
            raise ConnectorError(f"Crossref has no work {doi}") from None
        message = self._message(data)
        dois: list[str] = []
        unresolved = 0
        for ref in message.get("reference") or []:
            try:
                cited = doi_id(str(ref.get("DOI", ""))) if isinstance(ref, dict) else None
            except ConnectorError:
                cited = None
            if cited and "," in cited:
                cited = None  # a comma would split the filter list; leave it unresolved
            if cited and cited not in dois:
                dois.append(cited)
            elif not cited:
                unresolved += 1
        self.last_unresolved_references = unresolved
        self.last_related_truncated = len(dois) > self.max_related
        dois = dois[: self.max_related]
        found: list[PaperRecord] = []
        for start in range(0, len(dois), BATCH):
            chunk = dois[start : start + BATCH]
            params = self._params(filter=",".join(f"doi:{d}" for d in chunk), rows=len(chunk))
            found.extend(self._records(self._message(self._http.get_json("/works", params)).get("items")))
        return tuple(found)

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        raise NotSupportedError("crossref publishes only a cited-by count, not the citing works; use OpenAlex or OpenCitations")

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("crossref does not provide full text")
