"""CORE connector (official API v3, https://api.core.ac.uk/docs/v3).

CORE aggregates open-access research outputs from repositories and journals. `external_id` is
CORE's numeric work id. An API key is required (free registration); without one the connector
can't be built, and nothing falls back to anonymous access.

* Search: `/search/works` with `q=` (the `core` adapter writes AND / OR with quoted phrases) and
  offset paging. CORE searches full text as well as title and abstract, so a search matches more
  than a title/abstract search would; the adapter says so in its caveats. Offset paging stops at
  `MAX_OFFSET`; `last_search_capped` is then True.
* Full text: `get_fulltext` returns the text CORE holds for a work, with its download URL. CORE
  does not report a licence for it, so `license` is None ("not reported"), never guessed: check
  before storing or processing it (spec section 6, M2.7).
* No citation data, so references and cited-by raise `NotSupportedError`.

Everything returned is untrusted third-party text; nothing here writes to the database.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from app.connectors.base import ConnectorBase, ConnectorError, FullText, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy, NotFoundError
from app.doi import normalize_doi

BASE_URL = "https://api.core.ac.uk/v3"
NAME = "core"
MAX_PAGE = 100
MAX_OFFSET = 10000
MAX_FULLTEXT_CHARS = 2_000_000
SUPPORTED_FILTERS = ("year_from", "year_to")
_ID = re.compile(r"^\d{1,12}$")
_YEAR = re.compile(r"^\d{4}$")
_OFFSET = re.compile(r"^\d{1,6}$")


def work_id(value: str) -> str:
    """CORE's numeric work id, from `123` or a core.ac.uk works/outputs URL; anything else is refused."""
    text = (value or "").strip()
    text = re.sub(r"^https?://core\.ac\.uk/(works|outputs|download)/", "", text, flags=re.I).strip("/")
    if not _ID.match(text):
        raise ConnectorError("A CORE id is a number such as 123456789")
    return str(int(text))


def _str(value: Any, limit: int) -> str | None:
    return " ".join(value.split())[:limit] or None if isinstance(value, str) and value.strip() else None


def _http(value: Any) -> str | None:
    text = _str(value, 2000)
    return text if text and text.lower().startswith(("http://", "https://")) else None


def to_record(work: Any) -> PaperRecord | None:
    if not isinstance(work, dict):
        return None
    try:
        ident = work_id(str(work.get("id") or ""))
    except ConnectorError:
        return None
    title = _str(work.get("title"), 500)
    if not title:
        return None
    flags: list[str] = []
    try:
        doi = normalize_doi(work.get("doi") if isinstance(work.get("doi"), str) else None)
    except ValueError:
        doi = None
        flags.append("invalid_doi")
    authors = tuple(
        name for name in (_str(a.get("name"), 200) for a in (work.get("authors") or [])[:200] if isinstance(a, dict)) if name
    )
    year = work.get("yearPublished")
    year = year if isinstance(year, int) and 1000 <= year <= 2200 else None
    journals = work.get("journals") or []
    venue = _str(journals[0].get("title"), 500) if journals and isinstance(journals[0], dict) else None
    abstract = _str(work.get("abstract"), 20000)
    if not abstract:
        flags.append("no_abstract")
    links = {link.get("type"): _http(link.get("url")) for link in (work.get("links") or []) if isinstance(link, dict)}
    return PaperRecord(
        connector=NAME,
        external_id=ident,
        title=title,
        doi=doi,
        authors=authors,
        year=year,
        venue=venue or _str(work.get("publisher"), 500),
        abstract=abstract,
        url=links.get("display") or f"https://core.ac.uk/works/{ident}",
        oa_url=_http(work.get("downloadUrl")) or links.get("download"),
        source_ids={NAME: ident},
        quality_flags=tuple(flags),
    )


class CoreConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, api_key: SecretStr | None = None):
        key = api_key.get_secret_value().strip() if api_key else ""
        if http is None:
            if not key:
                raise ConnectorError("CORE needs an API key (CORE_API_KEY); there is no anonymous access")
            http = ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=1.0), headers={"Authorization": f"Bearer {key}"})
        self._http = http
        self.skipped_records = 0
        self.last_search_capped = False

    def _records(self, results: Any) -> list[PaperRecord]:
        if not isinstance(results, list):
            raise ConnectorError("CORE returned an unexpected reply")
        records = []
        for work in results:
            record = to_record(work)
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _query(query: str, filters: dict[str, Any]) -> str:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported CORE filter(s): " + ", ".join(unknown))
        parts = [f"({query})"] if filters else [query]
        for key, op in (("year_from", ">="), ("year_to", "<=")):
            if key in filters:
                value = filters[key]
                if isinstance(value, bool) or not _YEAR.match(str(value)):
                    raise ConnectorError(f"{key} must be a four-digit year")
                parts.append(f"yearPublished{op}{value}")
        return " AND ".join(parts)

    def _work(self, external_id: str) -> dict[str, Any] | None:
        ident = work_id(external_id)
        try:
            data = self._http.get_json(f"/works/{ident}")
        except NotFoundError:
            return None
        if not isinstance(data, dict):
            raise ConnectorError("CORE returned an unexpected reply")
        return data

    def search(self, request: SearchRequest) -> SearchPage:
        offset = 0
        if request.cursor is not None:
            if not _OFFSET.match(request.cursor):
                raise ConnectorError("Invalid cursor for CORE")
            offset = int(request.cursor)
        limit = min(request.limit, MAX_PAGE, MAX_OFFSET - offset)
        if limit < 1:
            raise ConnectorError("CORE search cannot page past the first 10,000 results")
        data = self._http.get_json("/search/works", {"q": self._query(request.query, request.filters), "limit": limit, "offset": offset})
        if not isinstance(data, dict):
            raise ConnectorError("CORE returned an unexpected reply")
        results = data.get("results") if data.get("results") is not None else []
        records = self._records(results)
        total = data.get("totalHits")
        total = total if isinstance(total, int) and total >= 0 else None
        nxt = offset + len(results)
        more = bool(results) and (total is None or nxt < total)
        self.last_search_capped = more and nxt >= MAX_OFFSET
        return SearchPage(records=tuple(records), total=total, next_cursor=str(nxt) if more and nxt < MAX_OFFSET else None)

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        work = self._work(external_id)
        if work is None:
            return None
        record = to_record(work)
        if record is None:
            raise ConnectorError("CORE returned a work that cannot be represented (no id or title)")
        return record

    def get_fulltext(self, external_id: str) -> FullText | None:
        """The full text CORE holds, or None when it has none. The licence is not reported by CORE."""
        work = self._work(external_id)
        if work is None:
            return None
        text = work.get("fullText")
        if not isinstance(text, str) or not text.strip():
            return None
        if len(text) > MAX_FULLTEXT_CHARS:
            raise ConnectorError(f"CORE full text is over {MAX_FULLTEXT_CHARS:,} characters; refused rather than cut short")
        source = _http(work.get("downloadUrl")) or f"https://core.ac.uk/works/{work_id(external_id)}"
        return FullText(source_url=source, text=text, license=None)
