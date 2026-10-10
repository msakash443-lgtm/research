"""arXiv connector (official Atom API, https://info.arxiv.org/help/api/).

Search and look-up by id. `external_id` is the arXiv id without its version (`2101.00001`, or the
old style `hep-th/9901001`). Every record is flagged `preprint`: arXiv papers are not peer reviewed.

* The API returns Atom XML. A reply containing a DOCTYPE or ENTITY declaration is refused before it is
  parsed (entity-expansion attacks), and nothing is fetched from inside the XML.
* arXiv asks clients to leave about 3 seconds between requests; the default policy does.
* `oa_url` is the PDF address. This connector does **not** download PDFs: fetching and storing a
  file is a licence-checked step of its own (M1.4.8 / M2.7), so `get_fulltext` is not offered.
* No citation data exists in the API, so cited-by and references are not offered either.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from app.connectors.base import ConnectorBase, ConnectorError, FullText, NotSupportedError, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.doi import normalize_doi

BASE_URL = "https://export.arxiv.org/api"
NAME = "arxiv"
MAX_ENTRIES = 200
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom", "os": "http://a9.com/-/spec/opensearch/1.1/"}
_NEW_ID = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_OLD_ID = re.compile(r"^[a-z][a-z\-]*(\.[A-Z]{2})?/\d{7}(v\d+)?$")
_VERSION = re.compile(r"v\d+$")
_OAI_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "raw": "http://arxiv.org/OAI/arXivRaw/"}
_ARXIV_VERSION = re.compile(r"^v\d+$")


@dataclass(frozen=True)
class ArxivLicense:
    uri: str | None
    version: str


def parse_license_record(xml: str, external_id: str) -> ArxivLicense | None:
    """Read the current version and licence from arXiv's OAI-PMH arXivRaw record."""
    if "<!doctype" in xml.lower() or "<!entity" in xml.lower():
        raise ConnectorError("arxiv: licence reply contained a DOCTYPE or ENTITY declaration and was refused")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ConnectorError("arxiv: licence reply was not valid XML") from None
    if root.tag != "{http://www.openarchives.org/OAI/2.0/}OAI-PMH":
        raise ConnectorError("arxiv: licence reply was not an OAI-PMH response")

    get_record = root.find("oai:GetRecord", _OAI_NS)
    if get_record is None:
        raise ConnectorError("arxiv: OAI-PMH licence reply had no GetRecord response")
    error = get_record.find("oai:error", _OAI_NS)
    if error is not None:
        if error.get("code") == "idDoesNotExist":
            return None
        raise ConnectorError("arxiv: OAI-PMH licence lookup was rejected")

    record_envelope = get_record.find("oai:record", _OAI_NS)
    if record_envelope is None:
        raise ConnectorError("arxiv: OAI-PMH licence reply had no record")
    header = record_envelope.find("oai:header", _OAI_NS)
    if header is None:
        raise ConnectorError("arxiv: OAI-PMH licence reply had no record header")
    expected_id = f"oai:arXiv.org:{normalise_id(external_id)}"
    header_id = _text(header.find("oai:identifier", _OAI_NS), 200)
    if header_id != expected_id:
        raise ConnectorError("arxiv: OAI-PMH licence record did not match the requested id")
    if header.get("status") == "deleted":
        return None
    record = record_envelope.find("oai:metadata/raw:arXivRaw", _OAI_NS)
    if record is None:
        return None
    record_id = _text(record.find("raw:id", _OAI_NS), 100) or ""
    try:
        record_id = normalise_id(record_id)
    except ConnectorError:
        raise ConnectorError("arxiv: OAI-PMH licence record had an invalid id") from None
    if record_id != normalise_id(external_id):
        raise ConnectorError("arxiv: OAI-PMH licence record did not match the requested id")

    versions = record.findall("raw:version", _OAI_NS)
    version = versions[-1].get("version") if versions else None
    if not version or not _ARXIV_VERSION.fullmatch(version):
        raise ConnectorError("arxiv: OAI-PMH licence record had an invalid current version")
    uri = _text(record.find("raw:license", _OAI_NS), 500)
    return ArxivLicense(uri=uri, version=version)


def normalise_id(value: str) -> str:
    """The bare arXiv id (no version, no URL prefix, no `arXiv:`); raises ConnectorError if it isn't one."""
    text = (value or "").strip()
    text = re.sub(r"^(arxiv:|https?://arxiv\.org/(abs|pdf)/)", "", text, flags=re.I)
    text = re.sub(r"\.pdf$", "", text)
    if not (_NEW_ID.match(text) or _OLD_ID.match(text)):
        raise ConnectorError("An arXiv id looks like 2101.00001 or hep-th/9901001")
    return _VERSION.sub("", text)


def _text(node: ET.Element | None, limit: int) -> str | None:
    if node is None or node.text is None:
        return None
    clean = " ".join(node.text.split())
    return clean[:limit] or None


def parse_feed(xml: str) -> tuple[list[PaperRecord], int | None]:
    """(records, reported total) from an Atom reply. Entries that can't be represented (no title) are skipped."""
    head = xml[:4096].lower()
    if "<!doctype" in head or "<!entity" in xml.lower():
        raise ConnectorError("arxiv: reply contained a DOCTYPE or ENTITY declaration and was refused")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ConnectorError("arxiv: reply was not valid XML") from None
    total_node = root.find("os:totalResults", _NS)
    total = int(total_node.text) if total_node is not None and (total_node.text or "").strip().isdigit() else None
    records: list[PaperRecord] = []
    for entry in root.findall("a:entry", _NS)[:MAX_ENTRIES]:
        entry_id = _text(entry.find("a:id", _NS), 300) or ""
        if "/api/errors" in entry_id:
            raise ConnectorError("arxiv: the search was rejected (" + (_text(entry.find("a:summary", _NS), 200) or "no detail") + ")")
        title = _text(entry.find("a:title", _NS), 500)
        try:
            ident = normalise_id(entry_id)
        except ConnectorError:
            continue
        if not title:
            continue
        doi = None
        try:
            doi = normalize_doi(_text(entry.find("arxiv:doi", _NS), 300))
        except ValueError:
            pass
        published = _text(entry.find("a:published", _NS), 10) or ""
        authors = tuple(n for n in (_text(a.find("a:name", _NS), 200) for a in entry.findall("a:author", _NS)[:200]) if n)
        records.append(
            PaperRecord(
                connector=NAME,
                external_id=ident,
                title=title,
                doi=doi,
                authors=authors,
                year=int(published[:4]) if published[:4].isdigit() and 1000 <= int(published[:4]) <= 2200 else None,
                venue=_text(entry.find("arxiv:journal_ref", _NS), 500) or "arXiv",
                abstract=_text(entry.find("a:summary", _NS), 20000),
                url=f"https://arxiv.org/abs/{ident}",
                oa_url=f"https://arxiv.org/pdf/{ident}",
                source_ids={NAME: ident},
                quality_flags=("preprint",) + (() if _text(entry.find("a:summary", _NS), 5) else ("no_abstract",)),
            )
        )
    return records, total


class ArxivConnector(ConnectorBase):
    name = NAME

    def __init__(self, http: ConnectorHttpClient | None = None, license_http: ConnectorHttpClient | None = None):
        self._http = http or ConnectorHttpClient(NAME, BASE_URL, HttpPolicy(min_interval_seconds=3.0))
        self._license_http = license_http

    def search(self, request: SearchRequest) -> SearchPage:
        try:
            start = int(request.cursor or 0)
        except ValueError:
            raise ConnectorError("arxiv: invalid cursor") from None
        if start < 0:
            raise ConnectorError("arxiv: invalid cursor")
        params: dict[str, Any] = {
            "search_query": request.query,
            "start": start,
            "max_results": min(request.limit, MAX_ENTRIES),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        records, total = parse_feed(self._http.get_text("/query", params))
        nxt = start + len(records)
        more = bool(records) and (total is None or nxt < total)
        return SearchPage(records=tuple(records), total=total, next_cursor=str(nxt) if more else None)

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        ident = normalise_id(external_id)
        records, _ = parse_feed(self._http.get_text("/query", {"id_list": ident, "max_results": 1}))
        return records[0] if records else None

    def get_license(self, external_id: str) -> ArxivLicense | None:
        ident = normalise_id(external_id)
        if self._license_http is None:
            self._license_http = ConnectorHttpClient(
                NAME, "https://oaipmh.arxiv.org", HttpPolicy(min_interval_seconds=3.0)
            )
        xml = self._license_http.get_text(
            "/oai", {"verb": "GetRecord", "identifier": f"oai:arXiv.org:{ident}", "metadataPrefix": "arXivRaw"}
        )
        return parse_license_record(xml, ident)

    def get_citations(self, external_id: str):
        raise NotSupportedError("arXiv has no citation data")

    def get_references(self, external_id: str):
        raise NotSupportedError("arXiv has no citation data")

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("arXiv PDFs are fetched in a licence-checked step of their own, not by the connector")
