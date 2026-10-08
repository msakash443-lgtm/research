"""OpenCitations connector (official REST APIs, https://api.opencitations.net).

Open citation data: the Index API (v2) lists who cites what, the Meta API (v1) gives each work's
bibliographic metadata. `external_id` is the bare lower-case DOI, or `omid:br/...` (OpenCitations'
own id) for a work without a DOI.

* There is no keyword search, so `search` raises `NotSupportedError`; this connector is for
  look-up and citation chasing (snowballing, spec 5.2.3).
* `get_references` / `get_citations` read the Index, then fetch metadata for the linked works from
  Meta in batches. A linked work Meta has no title for can't be a record; the number of such works
  is left in `last_unresolved_related`. At most `max_related` are returned (`last_related_truncated`).
* An access token is optional (OpenCitations asks for one for heavy use); it is sent as the
  `authorization` header when configured.
* No full text.

Everything returned is untrusted third-party text; nothing here writes to the database.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr

from app.connectors.base import ConnectorBase, ConnectorError, FullText, NotSupportedError, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy, NotFoundError
from app.doi import normalize_doi

BASE_URL = "https://api.opencitations.net"
NAME = "opencitations"
META_BATCH = 20
_OMID = re.compile(r"^omid:br/0\d{1,20}$")
_BRACKETS = re.compile(r"\s*\[[^\]]*\]")
_SAFE = "/()-._:;"


def oc_id(value: str) -> str:
    """A bare DOI (from a DOI, `doi:` or doi.org form) or `omid:br/...`; anything else is refused."""
    text = (value or "").strip()
    if _OMID.match(text.lower()):
        return text.lower()
    try:
        doi = normalize_doi(text)
    except ValueError:
        doi = None
    if not doi:
        raise ConnectorError("An OpenCitations id is a DOI or omid:br/...")
    return doi


def _query_id(ident: str) -> str:
    return ident if ident.startswith("omid:") else f"doi:{ident}"


def _ids(field: Any) -> dict[str, str]:
    """`"doi:10.1/x omid:br/061 pmid:9"` -> {"doi": "10.1/x", "omid": "br/061", "pmid": "9"} (first of each kind)."""
    found: dict[str, str] = {}
    for token in (field or "").split() if isinstance(field, str) else []:
        kind, _, value = token.partition(":")
        if value and kind not in found:
            found[kind] = value
    return found


def _identity(field: Any) -> str | None:
    """The id this connector uses for a work: its DOI if valid, else its OMID."""
    ids = _ids(field)
    try:
        doi = normalize_doi(ids.get("doi"))
    except ValueError:
        doi = None
    if doi:
        return doi
    omid = f"omid:{ids['omid']}".lower() if "omid" in ids else ""
    return omid if _OMID.match(omid) else None


def _clean(value: Any, limit: int) -> str | None:
    return " ".join(value.split())[:limit] or None if isinstance(value, str) and value.strip() else None


def _authors(field: Any) -> tuple[str, ...]:
    names = []
    for part in (field or "").split(";")[:200] if isinstance(field, str) else []:
        part = _BRACKETS.sub("", part).strip()
        family, _, given = part.partition(",")
        name = f"{given.strip()} {family.strip()}".strip() if given.strip() else family.strip()
        if name:
            names.append(name[:200])
    return tuple(names)


def to_record(item: Any) -> PaperRecord | None:
    """One Meta entry as a record, or None when it has no usable id or no title."""
    if not isinstance(item, dict):
        return None
    ident = _identity(item.get("id"))
    title = _clean(item.get("title"), 500)
    if not ident or not title:
        return None
    ids = _ids(item.get("id"))
    source_ids = {NAME: ident}
    if ids.get("openalex", "").upper().startswith("W"):
        source_ids["openalex"] = ids["openalex"].upper()[:50]
    if ids.get("pmid", "").isdigit():
        source_ids["pmid"] = ids["pmid"][:20]
    date = item.get("pub_date") if isinstance(item.get("pub_date"), str) else ""
    year = int(date[:4]) if date[:4].isdigit() and 1000 <= int(date[:4]) <= 2200 else None
    doi = ident if not ident.startswith("omid:") else None
    venue = _clean(_BRACKETS.sub("", item["venue"]), 500) if isinstance(item.get("venue"), str) else None
    flags = ("no_abstract",)  # Meta has no abstracts
    return PaperRecord(
        connector=NAME, external_id=ident, title=title, doi=doi, authors=_authors(item.get("author")), year=year,
        venue=venue, url=f"https://doi.org/{doi}" if doi else None, source_ids=source_ids, quality_flags=flags,
    )


class OpenCitationsConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, access_token: SecretStr | None = None, max_related: int = 200):
        if max_related < 1:
            raise ValueError("max_related must be at least 1")
        token = access_token.get_secret_value().strip() if access_token else ""
        self._http = http or ConnectorHttpClient(
            NAME, BASE_URL, HttpPolicy(min_interval_seconds=0.2), headers={"authorization": token} if token else None
        )
        self.max_related = max_related
        self.skipped_records = 0
        self.last_related_truncated = False
        self.last_unresolved_related = 0

    def _meta(self, idents: list[str]) -> dict[str, PaperRecord]:
        found: dict[str, PaperRecord] = {}
        for start in range(0, len(idents), META_BATCH):
            chunk = "__".join(quote(_query_id(i), safe=_SAFE) for i in idents[start : start + META_BATCH])
            try:
                data = self._http.get_json(f"/meta/v1/metadata/{chunk}")
            except NotFoundError:
                continue
            if not isinstance(data, list):
                raise ConnectorError("OpenCitations Meta returned an unexpected reply")
            for item in data:
                record = to_record(item)
                if record is None:
                    self.skipped_records += 1
                else:
                    found[record.external_id] = record
                    for kind, value in _ids(item.get("id")).items():  # also reachable by its OMID
                        if kind == "omid":
                            found.setdefault(f"omid:{value}".lower(), record)
        return found

    def _related(self, external_id: str, kind: str, side: str) -> tuple[PaperRecord, ...]:
        ident = oc_id(external_id)
        self.last_related_truncated = False
        self.last_unresolved_related = 0
        try:
            rows = self._http.get_json(f"/index/v2/{kind}/{quote(_query_id(ident), safe=_SAFE)}")
        except NotFoundError:
            raise ConnectorError(f"OpenCitations has no work {ident}") from None  # never an empty "no citations"
        if not isinstance(rows, list):
            raise ConnectorError("OpenCitations Index returned an unexpected reply")
        linked: list[str] = []
        for row in rows:
            other = _identity(row.get(side)) if isinstance(row, dict) else None
            if other is None:
                self.last_unresolved_related += 1
            elif other not in linked and other != ident:
                linked.append(other)
        if len(linked) > self.max_related:
            self.last_related_truncated = True
            linked = linked[: self.max_related]
        meta = self._meta(linked)
        records = []
        for other in linked:
            if other in meta:
                records.append(meta[other])
            else:
                self.last_unresolved_related += 1
        return tuple(records)

    def search(self, request: SearchRequest) -> SearchPage:
        raise NotSupportedError("opencitations has no keyword search; use it to look up works and their citations")

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        ident = oc_id(external_id)
        return self._meta([ident]).get(ident)

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works this one cites (Index `references`, the `cited` side)."""
        return self._related(external_id, "references", "cited")

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works that cite this one (Index `citations`, the `citing` side)."""
        return self._related(external_id, "citations", "citing")

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("opencitations does not provide full text")
