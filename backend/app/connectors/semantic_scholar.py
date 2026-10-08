"""Semantic Scholar connector (official Graph API, https://api.semanticscholar.org/api-docs).

Search, lookup by id (S2 paper id or DOI), batch lookup, references and citations (each paged).
`search` is relevance search (no operators); `boolean_search` is the bulk endpoint that applies
AND / OR / phrases, which database searches use (M1.5.5).
Full text is not offered (`get_fulltext` raises `NotSupportedError`); the open-access PDF link is
returned as `oa_url`.

Behaviour worth knowing:
* Search pages by offset; the cursor is the next offset as text. The API refuses relevance
  search beyond its first 1,000 results: when that limit stops paging, `last_search_capped`
  is True, so "no next page" isn't mistaken for "that was everything".
* Without an API key S2 allows very little traffic, so the default rate gate is slow (1.1 s
  between requests); with a key it is 0.1 s. The key comes from configuration and is only ever
  sent as a header, never put in a URL or an error.
* References/citations return at most `max_related` works and set `last_related_truncated`;
  entries S2 could not match to a paper (no id or title) are skipped and counted in
  `skipped_records`.
* Unknown or malformed filters raise before any request.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr

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

BASE_URL = "https://api.semanticscholar.org/graph/v1"
NAME = "semantic_scholar"
FIELDS = "paperId,title,abstract,year,venue,authors,externalIds,openAccessPdf,url,publicationTypes"
MAX_SEARCH_WINDOW = 1000  # offset + limit may not exceed this for relevance search
MAX_BATCH = 500
SUPPORTED_FILTERS = ("year_from", "year_to", "type", "is_oa")
_PAPER_ID = re.compile(r"^[0-9a-f]{40}$")
_YEAR = re.compile(r"^\d{4}$")
_TYPE = re.compile(r"^[A-Za-z]{3,40}$")
_OFFSET = re.compile(r"^\d{1,6}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-=+/.]{1,500}$")  # bulk-search continuation token


def paper_ref(value: str) -> str:
    """The identifier S2 expects: a 40-hex paper id, or `DOI:<doi>`. Anything else is refused."""
    text = (value or "").strip()
    if _PAPER_ID.match(text.lower()):
        return text.lower()
    try:
        doi = normalize_doi(text)
    except ValueError:
        doi = None
    if doi:
        return "DOI:" + doi
    raise ConnectorError("Semantic Scholar id must be a 40-character paper id or a DOI")


def _path_ref(ref: str) -> str:
    return quote(ref, safe="/()-._:;")


def _str(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _http(value: Any) -> str | None:
    text = _str(value, 2000)
    return text if text and text.lower().startswith(("http://", "https://")) else None


def to_record(paper: Any) -> PaperRecord | None:
    """Normalise one S2 paper; None when it has no id or title (S2 returns such stubs)."""
    if not isinstance(paper, dict):
        return None
    pid = paper.get("paperId")
    if not isinstance(pid, str) or not _PAPER_ID.match(pid.lower()):
        return None
    title = _str(paper.get("title"), 500)
    if not title:
        return None
    pid = pid.lower()

    external = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    flags = []
    doi = None
    if external.get("DOI"):
        try:
            doi = normalize_doi(str(external["DOI"]))
        except ValueError:
            flags.append("invalid_doi")
    source_ids = {NAME: pid}
    for key, label in (("ArXiv", "arxiv"), ("PubMed", "pmid"), ("PubMedCentral", "pmcid"), ("CorpusId", "corpusid")):
        value = external.get(key)
        if value not in (None, ""):
            source_ids[label] = str(value)[:100]

    abstract = _str(paper.get("abstract"), 20000)
    venue = _str(paper.get("venue"), 500)
    if (venue or "").lower() == "arxiv.org":
        flags.append("preprint")
    if not abstract:
        flags.append("no_abstract")
    year = paper.get("year")
    pdf = paper.get("openAccessPdf") if isinstance(paper.get("openAccessPdf"), dict) else {}
    authors = tuple(
        name
        for a in (paper.get("authors") or [])[:200]
        if isinstance(a, dict) and (name := _str(a.get("name"), 200))
    )
    return PaperRecord(
        connector=NAME,
        external_id=pid,
        title=title,
        doi=doi,
        authors=authors,
        year=year if isinstance(year, int) and 1000 <= year <= 2200 else None,
        venue=venue,
        abstract=abstract,
        url=_http(paper.get("url")),
        oa_url=_http(pdf.get("url")),
        source_ids=source_ids,
        quality_flags=tuple(flags),
    )


class SemanticScholarConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, api_key: SecretStr | None = None, max_related: int = 200):
        if max_related < 1:
            raise ValueError("max_related must be at least 1")
        key = api_key.get_secret_value().strip() if api_key else ""
        if http is None:
            interval = 0.1 if key else 1.1
            http = ConnectorHttpClient(
                NAME, BASE_URL, HttpPolicy(min_interval_seconds=interval), headers={"x-api-key": key} if key else None
            )
        self._http = http
        self.max_related = max_related
        self.skipped_records = 0
        self.last_related_truncated = False
        self.last_search_capped = False

    # --- helpers -------------------------------------------------------------

    def _records(self, papers: Any) -> list[PaperRecord]:
        if not isinstance(papers, list):
            raise ConnectorError("Semantic Scholar returned an unexpected reply")
        records = []
        for paper in papers:
            record = to_record(paper)
            if record is None:
                self.skipped_records += 1
            else:
                records.append(record)
        return records

    @staticmethod
    def _search_params(filters: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported Semantic Scholar filter(s): " + ", ".join(unknown))
        params: dict[str, Any] = {}
        for key in ("year_from", "year_to"):
            if key in filters and (isinstance(filters[key], bool) or not _YEAR.match(str(filters[key]))):
                raise ConnectorError(f"{key} must be a four-digit year")
        if "year_from" in filters or "year_to" in filters:
            params["year"] = f"{filters.get('year_from', '')}-{filters.get('year_to', '')}"
        if "type" in filters:
            if not isinstance(filters["type"], str) or not _TYPE.match(filters["type"]):
                raise ConnectorError("type must be a publication type such as 'JournalArticle'")
            params["publicationTypes"] = filters["type"]
        if "is_oa" in filters:
            if filters["is_oa"] is not True:
                raise ConnectorError("is_oa can only be used as true (S2 cannot filter for 'not open access')")
            params["openAccessPdf"] = ""
        return params

    def _paged(self, path: str, key: str) -> list[PaperRecord]:
        """Offset-page a references/citations endpoint, up to `max_related` papers."""
        self.last_related_truncated = False
        found: list[PaperRecord] = []
        offset = 0
        while True:
            data = self._http.get_json(path, {"fields": FIELDS, "offset": offset, "limit": min(100, self.max_related - len(found))})
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ConnectorError("Semantic Scholar returned an unexpected reply")
            rows = data["data"]
            found.extend(self._records([row.get(key) if isinstance(row, dict) else None for row in rows]))
            nxt = data.get("next")
            if not rows or not isinstance(nxt, int) or nxt <= offset:
                return found
            if len(found) >= self.max_related:
                self.last_related_truncated = True
                return found[: self.max_related]
            offset = nxt

    # --- Connector protocol ----------------------------------------------------

    def search(self, request: SearchRequest) -> SearchPage:
        offset = 0
        if request.cursor is not None:
            if not _OFFSET.match(request.cursor):
                raise ConnectorError("Invalid cursor for Semantic Scholar")
            offset = int(request.cursor)
        limit = min(request.limit, 100)
        if offset + limit > MAX_SEARCH_WINDOW:
            limit = MAX_SEARCH_WINDOW - offset
        if limit < 1:
            raise ConnectorError("Semantic Scholar does not return search results beyond the first 1,000")
        params = self._search_params(request.filters)
        params.update(query=request.query, offset=offset, limit=limit, fields=FIELDS)
        data = self._http.get_json("/paper/search", params)
        if not isinstance(data, dict):
            raise ConnectorError("Semantic Scholar returned an unexpected reply")
        records = self._records(data.get("data") if data.get("data") is not None else [])
        total = data.get("total")
        nxt = data.get("next")
        has_next = isinstance(nxt, int) and nxt > offset
        self.last_search_capped = has_next and nxt >= MAX_SEARCH_WINDOW
        return SearchPage(
            records=tuple(records),
            total=total if isinstance(total, int) and total >= 0 else None,
            next_cursor=str(nxt) if has_next and nxt < MAX_SEARCH_WINDOW else None,
        )

    def boolean_search(self, request: SearchRequest) -> SearchPage:
        """Boolean search over titles and abstracts (`/paper/search/bulk`, M1.5.5).

        Takes the bulk syntax the `semantic_scholar` adapter writes (`+` AND, `|` OR, quoted phrases,
        `*` prefix). Results are not ranked by relevance. Each page holds up to 1,000 records
        whatever `limit` says (the API has no page size); the cursor is the API's continuation token,
        so there is no 1,000-result window here. `search` stays the relevance search used for
        title look-ups (the citation verifier).
        """
        params = self._search_params(request.filters)
        params.update(query=request.query, fields=FIELDS)
        if request.cursor is not None:
            if not _TOKEN.match(request.cursor):
                raise ConnectorError("Invalid cursor for Semantic Scholar bulk search")
            params["token"] = request.cursor
        data = self._http.get_json("/paper/search/bulk", params)
        if not isinstance(data, dict):
            raise ConnectorError("Semantic Scholar returned an unexpected reply")
        records = self._records(data.get("data") if data.get("data") is not None else [])
        total = data.get("total")
        token = data.get("token")
        self.last_search_capped = False
        return SearchPage(
            records=tuple(records),
            total=total if isinstance(total, int) and total >= 0 else None,
            next_cursor=token if isinstance(token, str) and _TOKEN.match(token) and data.get("data") else None,
        )

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        ref = paper_ref(external_id)
        try:
            data = self._http.get_json(f"/paper/{_path_ref(ref)}", {"fields": FIELDS})
        except NotFoundError:
            return None
        record = to_record(data)
        if record is None:
            raise ConnectorError("Semantic Scholar returned a paper that cannot be represented (no id or title)")
        return record

    def get_many(self, external_ids: list[str]) -> tuple[PaperRecord | None, ...]:
        """Batch lookup; the result lines up with the input, with None where S2 has no such paper."""
        if not external_ids:
            return ()
        if len(external_ids) > MAX_BATCH:
            raise ConnectorError(f"At most {MAX_BATCH} ids per batch")
        refs = [paper_ref(i) for i in external_ids]
        data = self._http.post_json("/paper/batch", {"ids": refs}, {"fields": FIELDS})
        if not isinstance(data, list) or len(data) != len(refs):
            raise ConnectorError("Semantic Scholar returned an unexpected batch reply")
        result = []
        for item in data:
            record = to_record(item)
            if item is not None and record is None:
                self.skipped_records += 1
            result.append(record)
        return tuple(result)

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        ref = paper_ref(external_id)
        try:
            return tuple(self._paged(f"/paper/{_path_ref(ref)}/citations", "citingPaper"))
        except NotFoundError:
            raise ConnectorError("Semantic Scholar has no such paper") from None

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        ref = paper_ref(external_id)
        try:
            return tuple(self._paged(f"/paper/{_path_ref(ref)}/references", "citedPaper"))
        except NotFoundError:
            raise ConnectorError("Semantic Scholar has no such paper") from None

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("semantic_scholar does not provide full text; use an open-access connector")
