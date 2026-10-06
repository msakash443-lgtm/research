"""OpenAlex connector (official API, https://docs.openalex.org).

Search with filters and cursor paging, lookup by id, references (`referenced_works`), cited-by
(`filter=cites:`) and the open-access location. Full text is not offered by OpenAlex (use
Unpaywall/arXiv/CORE), so `get_fulltext` raises `NotSupportedError`.

Everything returned is untrusted third-party text; nothing here writes to the database.

Behaviour worth knowing:
* IDs are validated (`W123` or the openalex.org URL) before they go into a path or filter.
* Unknown search filters raise `ConnectorError` instead of being dropped, so a search never runs
  wider than the scholar asked for.
* A work with no title cannot be a `PaperRecord`; it is skipped and counted in
  `skipped_records`. A malformed DOI is dropped and the record is flagged `invalid_doi`.
* `get_citations`/`get_references` return at most `max_related` works; when more exist,
  `last_related_truncated` is True after the call.
"""

from __future__ import annotations

import re
from typing import Any

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

BASE_URL = "https://api.openalex.org"
NAME = "openalex"
_ID = re.compile(r"^W\d{1,15}$")
_TYPE = re.compile(r"^[a-z][a-z-]{0,30}$")
_DATE_YEAR = re.compile(r"^\d{4}$")
BATCH = 50  # ids per `openalex:W1|W2|...` filter
MAX_AUTHORS = 200
SUPPORTED_FILTERS = ("year_from", "year_to", "type", "is_oa")


def work_id(value: str) -> str:
    """`W123` from `W123` or `https://openalex.org/W123`; anything else is refused."""
    text = (value or "").strip()
    if text.lower().startswith(("https://openalex.org/", "http://openalex.org/")):
        text = text.rsplit("/", 1)[-1]
    text = text.upper()
    if not _ID.match(text):
        raise ConnectorError("OpenAlex id must look like W123456")
    return text


def _abstract(index: Any) -> str | None:
    """Rebuild the abstract from OpenAlex's inverted index ({word: [positions]})."""
    if not isinstance(index, dict) or not index:
        return None
    slots: dict[int, str] = {}
    for word, positions in index.items():
        if isinstance(word, str) and isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, int) and 0 <= pos < 20000:
                    slots[pos] = word
    text = " ".join(slots[i] for i in sorted(slots)).strip()
    return text[:20000] or None


def _short_id(url: Any) -> str | None:
    if isinstance(url, str) and url.strip():
        return url.strip().rstrip("/").rsplit("/", 1)[-1][:100] or None
    return None


def _str(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _http(value: Any) -> str | None:
    text = _str(value, 2000)
    return text if text and text.lower().startswith(("http://", "https://")) else None


def to_record(work: dict[str, Any]) -> PaperRecord | None:
    """Normalise one OpenAlex work; None when it cannot be represented (no usable id/title)."""
    try:
        external_id = work_id(str(work.get("id", "")))
    except ConnectorError:
        return None
    title = _str(work.get("display_name") or work.get("title"), 500)
    if not title:
        return None

    flags: list[str] = []
    doi = None
    if work.get("doi"):
        try:
            doi = normalize_doi(str(work["doi"]))
        except ValueError:
            flags.append("invalid_doi")

    authors = tuple(
        name
        for a in (work.get("authorships") or [])[:MAX_AUTHORS]
        if isinstance(a, dict) and (name := _str((a.get("author") or {}).get("display_name"), 200))
    )
    year = work.get("publication_year")
    year = year if isinstance(year, int) and 1000 <= year <= 2200 else None
    primary = work.get("primary_location") or {}
    venue = _str((primary.get("source") or {}).get("display_name"), 500)
    abstract = _abstract(work.get("abstract_inverted_index"))
    oa = work.get("open_access") or {}
    oa_url = _http(oa.get("oa_url")) or _http((work.get("best_oa_location") or {}).get("landing_page_url"))

    source_ids = {NAME: external_id}
    ids = work.get("ids") or {}
    for key in ("pmid", "pmcid", "mag"):
        value = _short_id(ids.get(key))
        if value:
            source_ids[key] = value

    if work.get("is_retracted"):
        flags.append("retracted")
    if work.get("type") == "preprint":
        flags.append("preprint")
    if not abstract:
        flags.append("no_abstract")

    return PaperRecord(
        connector=NAME,
        external_id=external_id,
        title=title,
        doi=doi,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        url=_http(primary.get("landing_page_url")),
        oa_url=oa_url,
        source_ids=source_ids,
        quality_flags=tuple(flags),
    )


class OpenAlexConnector(ConnectorBase):
    name = NAME

    def __init__(
        self,
        http: ConnectorHttpClient | None = None,
        contact_email: str | None = None,
        max_related: int = 200,
    ):
        if max_related < 1:
            raise ValueError("max_related must be at least 1")
        self._http = http or ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=0.15))
        self._mailto = contact_email.strip() if contact_email and contact_email.strip() else None
        self.max_related = max_related
        self.skipped_records = 0
        self.last_related_truncated = False

    # --- helpers -------------------------------------------------------------

    def _params(self, **params: Any) -> dict[str, Any]:
        if self._mailto:
            params["mailto"] = self._mailto  # OpenAlex "polite pool"
        return params

    def _records(self, results: Any) -> list[PaperRecord]:
        if not isinstance(results, list):
            raise ConnectorError("OpenAlex returned an unexpected reply")
        records = []
        for work in results:
            record = to_record(work) if isinstance(work, dict) else None
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _filter(filters: dict[str, Any]) -> str:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported OpenAlex filter(s): " + ", ".join(unknown))
        parts = []
        for key, value in filters.items():
            if key in ("year_from", "year_to"):
                if isinstance(value, bool) or not _DATE_YEAR.match(str(value)):
                    raise ConnectorError(f"{key} must be a four-digit year")
                parts.append(f"from_publication_date:{value}-01-01" if key == "year_from" else f"to_publication_date:{value}-12-31")
            elif key == "type":
                if not isinstance(value, str) or not _TYPE.match(value):
                    raise ConnectorError("type must be a lower-case work type such as 'article'")
                parts.append(f"type:{value}")
            elif key == "is_oa":
                if not isinstance(value, bool):
                    raise ConnectorError("is_oa must be true or false")
                parts.append(f"is_oa:{str(value).lower()}")
        return ",".join(parts)

    def _list(self, filter_: str, cursor: str, per_page: int) -> dict[str, Any]:
        data = self._http.get_json("/works", self._params(filter=filter_, **{"per-page": per_page, "cursor": cursor}))
        if not isinstance(data, dict):
            raise ConnectorError("OpenAlex returned an unexpected reply")
        return data

    # --- Connector protocol ----------------------------------------------------

    def search(self, request: SearchRequest) -> SearchPage:
        params = self._params(search=request.query, **{"per-page": request.limit, "cursor": request.cursor or "*"})
        filter_ = self._filter(request.filters)
        if filter_:
            params["filter"] = filter_
        data = self._http.get_json("/works", params)
        if not isinstance(data, dict):
            raise ConnectorError("OpenAlex returned an unexpected reply")
        meta = data.get("meta") or {}
        total = meta.get("count")
        cursor = meta.get("next_cursor")
        return SearchPage(
            records=tuple(self._records(data.get("results"))),
            total=total if isinstance(total, int) and total >= 0 else None,
            next_cursor=cursor if isinstance(cursor, str) and cursor else None,
        )

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        wid = work_id(external_id)
        try:
            work = self._http.get_json(f"/works/{wid}", self._params())
        except NotFoundError:
            return None
        if not isinstance(work, dict):
            raise ConnectorError("OpenAlex returned an unexpected reply")
        record = to_record(work)
        if record is None:
            raise ConnectorError("OpenAlex returned a work that cannot be represented (no title)")
        return record

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works that cite this one (`filter=cites:W...`), newest page first as OpenAlex orders them."""
        wid = work_id(external_id)
        self.last_related_truncated = False
        found: list[PaperRecord] = []
        cursor = "*"
        while cursor:
            data = self._list(f"cites:{wid}", cursor, min(100, self.max_related - len(found)))
            page = self._records(data.get("results"))
            found.extend(page)
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not data.get("results"):
                break  # an empty page ends paging even if a cursor is returned
            if len(found) >= self.max_related:
                self.last_related_truncated = bool(cursor)
                break
        return tuple(found[: self.max_related])

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works this one cites: its `referenced_works`, fetched in batches."""
        wid = work_id(external_id)
        try:
            work = self._http.get_json(f"/works/{wid}", self._params(select="id,referenced_works"))
        except NotFoundError:
            raise ConnectorError(f"OpenAlex has no work {wid}") from None
        if not isinstance(work, dict):
            raise ConnectorError("OpenAlex returned an unexpected reply")
        ids = []
        for url in work.get("referenced_works") or []:
            try:
                ids.append(work_id(str(url)))
            except ConnectorError:
                continue
        self.last_related_truncated = len(ids) > self.max_related
        ids = ids[: self.max_related]
        found: list[PaperRecord] = []
        for start in range(0, len(ids), BATCH):
            chunk = ids[start : start + BATCH]
            data = self._list("openalex:" + "|".join(chunk), "*", len(chunk))
            found.extend(self._records(data.get("results")))
        return tuple(found)

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("openalex does not provide full text; use an open-access connector (e.g. Unpaywall)")
