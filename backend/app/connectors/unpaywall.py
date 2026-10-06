"""Unpaywall connector (official REST API, https://unpaywall.org/products/api).

Maps a DOI to its open-access location. `external_id` is the bare lower-case DOI.

* `get_oa_location(doi)` is the main call: the best open-access copy (landing page and/or PDF,
  licence, version, host type), or `None` when Unpaywall knows the DOI but has no OA copy.
  A DOI Unpaywall has never heard of raises (it is not "closed access").
* `get_by_id(doi)` returns the metadata Unpaywall holds as a `PaperRecord` (with `oa_url`), or
  `None` for an unknown DOI. Unpaywall has no abstracts, so records carry `no_abstract`.
* Search, cited-by, references and full text are not offered: Unpaywall returns *links*, not
  text. Fetching and storing a PDF is a separate, licence-checked step (spec section 6:
  "check license before storing/processing"), so the licence is returned with every location.
* The API requires a contact email on every request. It comes from `CONNECTOR_CONTACT_EMAIL`;
  with none configured the connector refuses to be built instead of sending a made-up address.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

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

BASE_URL = "https://api.unpaywall.org/v2"
NAME = "unpaywall"
_EMAIL = re.compile(r"^[^\s@&#?=,]+@[^\s@&#?=,]+\.[^\s@&#?=,]+$")


class OaLocation(BaseModel):
    """An open-access copy of a work. The licence is as reported; check it before storing the file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doi: str
    landing_page_url: str | None = None
    pdf_url: str | None = None
    license: str | None = None  # e.g. "cc-by"; None means Unpaywall doesn't know, not "free to use"
    version: str | None = None  # publishedVersion / acceptedVersion / submittedVersion
    host_type: str | None = None  # publisher / repository


def _str(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _http(value: Any) -> str | None:
    text = _str(value, 2000)
    return text if text and text.lower().startswith(("http://", "https://")) else None


def _doi(value: str) -> str:
    try:
        doi = normalize_doi(value)
    except ValueError as exc:
        raise ConnectorError("Unpaywall id must be a DOI such as 10.1234/abc") from exc
    if not doi:
        raise ConnectorError("Unpaywall id must be a DOI such as 10.1234/abc")
    return doi


def to_location(data: dict[str, Any], doi: str) -> OaLocation | None:
    """The best OA location in an Unpaywall reply; None when the work has no OA copy."""
    best = data.get("best_oa_location")
    if not data.get("is_oa") or not isinstance(best, dict):
        return None
    landing, pdf = _http(best.get("url")), _http(best.get("url_for_pdf"))
    if not (landing or pdf):
        return None
    return OaLocation(
        doi=doi,
        landing_page_url=landing,
        pdf_url=pdf,
        license=_str(best.get("license"), 100),
        version=_str(best.get("version"), 50),
        host_type=_str(best.get("host_type"), 50),
    )


def to_record(data: dict[str, Any]) -> PaperRecord | None:
    try:
        doi = _doi(str(data.get("doi", "")))
    except ConnectorError:
        return None
    title = _str(data.get("title"), 500)
    if not title:
        return None
    location = to_location(data, doi)
    authors = []
    for a in (data.get("z_authors") or [])[:200]:
        if isinstance(a, dict):
            name = _str(a.get("raw_author_name"), 200) or " ".join(
                p for p in (_str(a.get("given"), 100), _str(a.get("family"), 100)) if p
            )
            if name:
                authors.append(name)
    year = data.get("year")
    return PaperRecord(
        connector=NAME,
        external_id=doi,
        title=title,
        doi=doi,
        authors=tuple(authors),
        year=year if isinstance(year, int) and 1000 <= year <= 2200 else None,
        venue=_str(data.get("journal_name"), 500),
        url=_http(data.get("doi_url")),
        oa_url=(location.pdf_url or location.landing_page_url) if location else None,
        source_ids={NAME: doi},
        quality_flags=("no_abstract",),
    )


class UnpaywallConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, contact_email: str | None = None):
        email = (contact_email or "").strip()
        if not _EMAIL.match(email):
            raise ConnectorError("Unpaywall needs a real contact email: set CONNECTOR_CONTACT_EMAIL")
        self._email = email
        self._http = http or ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=0.1))

    def _fetch(self, doi: str) -> dict[str, Any] | None:
        try:
            data = self._http.get_json("/" + quote(doi, safe="/()-._:;"), {"email": self._email})
        except NotFoundError:
            return None
        if not isinstance(data, dict) or data.get("error") is True:
            raise ConnectorError("Unpaywall returned an unexpected reply")
        return data

    def get_oa_location(self, doi: str) -> OaLocation | None:
        """The best open-access copy, or None if there is none. An unknown DOI raises."""
        normalised = _doi(doi)
        data = self._fetch(normalised)
        if data is None:
            raise ConnectorError("Unpaywall has no record of this DOI")
        return to_location(data, normalised)

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        data = self._fetch(_doi(external_id))
        if data is None:
            return None
        record = to_record(data)
        if record is None:
            raise ConnectorError("Unpaywall returned a record that cannot be represented (no title)")
        return record

    def search(self, request: SearchRequest) -> SearchPage:
        raise NotSupportedError("unpaywall resolves DOIs to open-access links; it is not used for searching")

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("unpaywall returns links, not text; use get_oa_location and fetch under the licence")
