"""PubMed connector: NCBI E-utilities for search and look-up, Europe PMC for references / cited-by.

Official APIs only: https://www.ncbi.nlm.nih.gov/books/NBK25501/ (E-utilities) and
https://europepmc.org/RestfulWebService. `external_id` is the PMID (digits).

* Search sends the query in PubMed syntax (the `pubmed` adapter writes `"phrase"[tiab]` terms):
  `esearch` returns PMIDs, then `efetch` returns the records as PubMed XML. ESearch can only page
  through the first 9,999 results; when that stops paging, `last_search_capped` is True so a
  capped search is never mistaken for a complete one.
* The XML reply carries NLM's public DOCTYPE line. That exact form (a public identifier, no
  internal subset) is removed before parsing; any other DOCTYPE or an ENTITY declaration is refused
  (entity-expansion attacks), and nothing is fetched from inside the XML.
* References and citations come from Europe PMC (`/MED/{pmid}/references|citations`). Only entries
  that are themselves PubMed records (source `MED`) can be returned as PMIDs; the others are
  counted in `last_unresolved_related`, not dropped silently. At most `max_related` are returned;
  `last_related_truncated` says when more exist.
* Full text is not offered (use Unpaywall/M2.7), so `get_fulltext` raises `NotSupportedError`.
* NCBI asks for `tool` and `email`; the configured contact email is sent when there is one. Without
  an API key NCBI allows 3 requests a second, which is the default spacing.

Everything returned is untrusted third-party text; nothing here writes to the database.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from pydantic import SecretStr

from app.connectors.base import ConnectorBase, ConnectorError, FullText, NotSupportedError, PaperRecord, SearchPage, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy, NotFoundError
from app.doi import normalize_doi

EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
NAME = "pubmed"
MAX_PAGE = 200
ESEARCH_WINDOW = 9999  # ESearch cannot return results past this position
EUROPEPMC_PAGE = 1000
SUPPORTED_FILTERS = ("year_from", "year_to")
_PMID = re.compile(r"^\d{1,9}$")
_YEAR = re.compile(r"^\d{4}$")
_OFFSET = re.compile(r"^\d{1,6}$")
# NLM's own DOCTYPE line: a public identifier and system URL, no internal subset.
_NLM_DOCTYPE = re.compile(r'<!DOCTYPE\s+PubmedArticleSet\s+PUBLIC\s+"[^"<>\[\]]*"\s+"[^"<>\[\]]*"\s*>', re.I)


def pmid(value: str) -> str:
    """The PMID from `123`, `pmid:123` or a pubmed.ncbi.nlm.nih.gov URL; anything else is refused."""
    text = (value or "").strip()
    text = re.sub(r"^(pmid:\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)", "", text, flags=re.I).strip("/")
    if not _PMID.match(text):
        raise ConnectorError("A PubMed id (PMID) is a number such as 31452104")
    return str(int(text))


def _clean(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    clean = " ".join(text.split())
    return clean[:limit] or None


def _all_text(node: ET.Element | None, limit: int) -> str | None:
    return _clean("".join(node.itertext()), limit) if node is not None else None


def _year(article: ET.Element) -> int | None:
    for path in ("Journal/JournalIssue/PubDate/Year", "ArticleDate/Year"):
        node = article.find(path)
        if node is not None and (node.text or "").strip()[:4].isdigit():
            year = int(node.text.strip()[:4])
            return year if 1000 <= year <= 2200 else None
    medline = article.find("Journal/JournalIssue/PubDate/MedlineDate")  # e.g. "2019 Jan-Feb"
    match = re.match(r"\s*(\d{4})", medline.text or "") if medline is not None else None
    return int(match.group(1)) if match and 1000 <= int(match.group(1)) <= 2200 else None


def _authors(article: ET.Element) -> tuple[str, ...]:
    names = []
    for author in article.findall("AuthorList/Author")[:200]:
        collective = _all_text(author.find("CollectiveName"), 200)
        last = _clean(author.findtext("LastName"), 100)
        first = _clean(author.findtext("ForeName"), 100) or _clean(author.findtext("Initials"), 20)
        name = collective or (f"{first} {last}" if first and last else last)
        if name:
            names.append(name)
    return tuple(names)


def _abstract(article: ET.Element) -> str | None:
    parts = []
    for node in article.findall("Abstract/AbstractText"):
        text = _all_text(node, 20000)
        if text:
            label = node.get("Label")
            parts.append(f"{label}: {text}" if label else text)
    return _clean(" ".join(parts), 20000)


def to_record(node: ET.Element) -> PaperRecord | None:
    """One `PubmedArticle` as a record, or None when it has no PMID or no title."""
    citation = node.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    if citation is None or article is None:
        return None
    try:
        ident = pmid(citation.findtext("PMID") or "")
    except ConnectorError:
        return None
    title = _all_text(article.find("ArticleTitle"), 500)
    if not title:
        return None
    ids = {i.get("IdType"): (i.text or "").strip() for i in node.findall("PubmedData/ArticleIdList/ArticleId")}
    flags: list[str] = []
    try:
        doi = normalize_doi(ids.get("doi") or None)
    except ValueError:
        doi = None
        flags.append("invalid_doi")
    types = {(t.text or "").strip() for t in article.findall("PublicationTypeList/PublicationType")}
    if "Retracted Publication" in types:
        flags.append("retracted")
    if "Preprint" in types:
        flags.append("preprint")
    abstract = _abstract(article)
    if not abstract:
        flags.append("no_abstract")
    source_ids = {NAME: ident}
    pmc = ids.get("pmc") or ""
    if re.match(r"^PMC\d{1,10}$", pmc):
        source_ids["pmcid"] = pmc
    return PaperRecord(
        connector=NAME,
        external_id=ident,
        title=title,
        doi=doi,
        authors=_authors(article),
        year=_year(article),
        venue=_all_text(article.find("Journal/Title"), 500),
        abstract=abstract,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{ident}/",
        oa_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/" if "pmcid" in source_ids else None,
        source_ids=source_ids,
        quality_flags=tuple(flags),
    )


def parse_articles(xml: str) -> tuple[list[PaperRecord], int]:
    """(records, skipped) from an efetch reply."""
    body = _NLM_DOCTYPE.sub("", xml, count=1)
    if "<!doctype" in body.lower() or "<!entity" in body.lower():
        raise ConnectorError("pubmed: reply contained a DOCTYPE or ENTITY declaration and was refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ConnectorError("pubmed: reply was not valid XML") from None
    records, skipped = [], 0
    for node in root.findall("PubmedArticle"):
        record = to_record(node)
        if record is None:
            skipped += 1
        else:
            records.append(record)
    return records, skipped


def _related_record(item: Any) -> PaperRecord | None:
    """A Europe PMC reference/citation entry as a record; None unless it is a titled PubMed record."""
    if not isinstance(item, dict) or item.get("source") != "MED":
        return None
    try:
        ident = pmid(str(item.get("id") or ""))
    except ConnectorError:
        return None
    title = _clean(item.get("title") if isinstance(item.get("title"), str) else None, 500)
    if not title:
        return None
    try:
        doi = normalize_doi(item.get("doi") if isinstance(item.get("doi"), str) else None)
    except ValueError:
        doi = None
    year_text = str(item.get("pubYear") or "")
    year = int(year_text) if _YEAR.match(year_text) and 1000 <= int(year_text) <= 2200 else None
    author_string = item.get("authorString") if isinstance(item.get("authorString"), str) else ""
    authors = tuple(a for a in (_clean(x, 200) for x in author_string.rstrip(".").split(",")[:200]) if a)
    venue = item.get("journalAbbreviation") if isinstance(item.get("journalAbbreviation"), str) else None
    return PaperRecord(
        connector=NAME, external_id=ident, title=title, doi=doi, authors=authors, year=year,
        venue=_clean(venue, 500), url=f"https://pubmed.ncbi.nlm.nih.gov/{ident}/", source_ids={NAME: ident},
    )


class PubMedConnector(ConnectorBase):
    name = NAME

    def __init__(
        self,
        http: ConnectorHttpClient | None = None,
        europepmc: ConnectorHttpClient | None = None,
        contact_email: str | None = None,
        api_key: SecretStr | None = None,
        max_related: int = 200,
    ):
        if max_related < 1:
            raise ValueError("max_related must be at least 1")
        key = api_key.get_secret_value().strip() if api_key else ""
        self._http = http or ConnectorHttpClient(NAME, EUTILS_URL, HttpPolicy(min_interval_seconds=0.11 if key else 0.34))
        self._epmc = europepmc or ConnectorHttpClient("europepmc", EUROPEPMC_URL, HttpPolicy(min_interval_seconds=0.1))
        self._identity: dict[str, str] = {"tool": "research-main"}
        if contact_email and contact_email.strip():
            self._identity["email"] = contact_email.strip()
        if key:
            self._identity["api_key"] = key
        self.max_related = max_related
        self.skipped_records = 0
        self.last_search_capped = False
        self.last_related_truncated = False
        self.last_unresolved_related = 0

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _filters(filters: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(filters) - set(SUPPORTED_FILTERS))
        if unknown:
            raise ConnectorError("Unsupported PubMed filter(s): " + ", ".join(unknown))
        params: dict[str, Any] = {}
        for key in SUPPORTED_FILTERS:
            if key in filters:
                value = filters[key]
                if isinstance(value, bool) or not _YEAR.match(str(value)):
                    raise ConnectorError(f"{key} must be a four-digit year")
        if filters:
            params = {"datetype": "pdat", "mindate": str(filters.get("year_from", "1000")), "maxdate": str(filters.get("year_to", "3000"))}
        return params

    def _fetch(self, ids: list[str]) -> list[PaperRecord]:
        if not ids:
            return []
        xml = self._http.get_text("/efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", **self._identity})
        records, skipped = parse_articles(xml)
        self.skipped_records += skipped
        order = {i: n for n, i in enumerate(ids)}
        return sorted(records, key=lambda r: order.get(r.external_id, len(order)))

    def _related(self, external_id: str, kind: str) -> tuple[PaperRecord, ...]:
        ident = pmid(external_id)
        list_key, item_key = ("referenceList", "reference") if kind == "references" else ("citationList", "citation")
        self.last_related_truncated = False
        self.last_unresolved_related = 0
        found: list[PaperRecord] = []
        page = 1
        while True:
            try:
                data = self._epmc.get_json(f"/MED/{ident}/{kind}", {"format": "json", "page": page, "pageSize": EUROPEPMC_PAGE})
            except NotFoundError:
                raise ConnectorError(f"Europe PMC has no PubMed record {ident}") from None
            if not isinstance(data, dict):
                raise ConnectorError("Europe PMC returned an unexpected reply")
            items = (data.get(list_key) or {}).get(item_key) or []
            if not isinstance(items, list):
                raise ConnectorError("Europe PMC returned an unexpected reply")
            for item in items:
                record = _related_record(item)
                if record is None:
                    self.last_unresolved_related += 1
                elif len(found) < self.max_related:
                    found.append(record)
                else:
                    self.last_related_truncated = True
            total = data.get("hitCount")
            if not items or not isinstance(total, int) or page * EUROPEPMC_PAGE >= total or self.last_related_truncated:
                return tuple(found)
            page += 1

    # --- Connector protocol ----------------------------------------------------

    def search(self, request: SearchRequest) -> SearchPage:
        start = 0
        if request.cursor is not None:
            if not _OFFSET.match(request.cursor):
                raise ConnectorError("Invalid cursor for PubMed")
            start = int(request.cursor)
        limit = min(request.limit, MAX_PAGE, ESEARCH_WINDOW - start)
        if limit < 1:
            raise ConnectorError("PubMed search cannot page past the first 9,999 results")
        params = {"db": "pubmed", "term": request.query, "retmode": "json", "retstart": start, "retmax": limit,
                  **self._filters(request.filters), **self._identity}
        data = self._http.get_json("/esearch.fcgi", params)
        result = data.get("esearchresult") if isinstance(data, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("idlist"), list):
            raise ConnectorError("PubMed returned an unexpected search reply")
        if result.get("ERROR"):
            raise ConnectorError("PubMed rejected the search: " + str(result["ERROR"])[:200])
        ids = [pmid(str(i)) for i in result["idlist"]]
        try:
            total = int(result.get("count"))
        except (TypeError, ValueError):
            total = None
        nxt = start + len(ids)
        more = bool(ids) and (total is None or nxt < total)
        self.last_search_capped = more and nxt >= ESEARCH_WINDOW
        return SearchPage(
            records=tuple(self._fetch(ids)),
            total=total if total is not None and total >= 0 else None,
            next_cursor=str(nxt) if more and nxt < ESEARCH_WINDOW else None,
        )

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        ident = pmid(external_id)
        records = self._fetch([ident])
        return records[0] if records else None

    def get_by_doi(self, doi: str) -> PaperRecord | None:
        """The PubMed record with this DOI, or None. Used to find a snowballing start paper (M1.9)."""
        try:
            bare = normalize_doi(doi)
        except ValueError:
            bare = None
        if not bare:
            raise ConnectorError("Not a valid DOI")
        data = self._http.get_json("/esearch.fcgi", {"db": "pubmed", "term": f'"{bare}"[doi]', "retmode": "json", "retmax": 2, **self._identity})
        result = data.get("esearchresult") if isinstance(data, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("idlist"), list):
            raise ConnectorError("PubMed returned an unexpected search reply")
        ids = [pmid(str(i)) for i in result["idlist"]]
        if len(ids) != 1:
            return None  # none, or ambiguous: never guess which record a DOI means
        records = self._fetch(ids)
        return records[0] if records and records[0].doi == bare else None

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works this one cites that are PubMed records (Europe PMC reference list)."""
        return self._related(external_id, "references")

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        """PubMed records that cite this one (Europe PMC citations)."""
        return self._related(external_id, "citations")

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise NotSupportedError("pubmed does not provide full text here; use an open-access connector (e.g. Unpaywall)")
