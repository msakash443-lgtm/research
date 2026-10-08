"""Plan M1.4.6: PubMed connector (NCBI E-utilities + Europe PMC references/citations). Offline only."""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.pubmed import ESEARCH_WINDOW, PubMedConnector, parse_articles, pmid

DOCTYPE = '<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2024//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_240101.dtd">\n'


def article(n=1, title="Remote work and <i>wellbeing</i>", doi="10.1000/P{n}", year="2021", types=("Journal Article",), abstract=True, pmc=None):
    ids = f'<ArticleId IdType="pubmed">{n}</ArticleId>'
    if doi:
        ids += f'<ArticleId IdType="doi">{doi.format(n=n)}</ArticleId>'
    if pmc:
        ids += f'<ArticleId IdType="pmc">{pmc}</ArticleId>'
    abstract_xml = (
        '<Abstract><AbstractText Label="BACKGROUND">Why it matters.</AbstractText><AbstractText Label="RESULTS">It helps.</AbstractText></Abstract>'
        if abstract else ""
    )
    types_xml = "".join(f"<PublicationType>{t}</PublicationType>" for t in types)
    return (
        f"<PubmedArticle><MedlineCitation><PMID>{n}</PMID><Article>"
        f"<Journal><JournalIssue><PubDate><Year>{year}</Year></PubDate></JournalIssue><Title>J Occup Health</Title></Journal>"
        f"<ArticleTitle>{title}</ArticleTitle>{abstract_xml}"
        "<AuthorList><Author><LastName>Lovelace</LastName><ForeName>Ada</ForeName></Author>"
        "<Author><CollectiveName>The Remote Work Group</CollectiveName></Author></AuthorList>"
        f"<PublicationTypeList>{types_xml}</PublicationTypeList></Article></MedlineCitation>"
        f"<PubmedData><ArticleIdList>{ids}</ArticleIdList></PubmedData></PubmedArticle>"
    )


def feed(*articles, doctype=DOCTYPE):
    return doctype + "<PubmedArticleSet>" + "".join(articles) + "</PubmedArticleSet>"


def make(eutils, europepmc=lambda r: {}, **kwargs):
    seen = []

    def wrap(handler):
        def inner(request):
            seen.append(request)
            result = handler(request)
            if isinstance(result, httpx.Response):
                return result
            return httpx.Response(200, text=result) if isinstance(result, str) else httpx.Response(200, json=result)
        return inner

    http = ConnectorHttpClient("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils", HttpPolicy(max_retries=0), transport=httpx.MockTransport(wrap(eutils)))
    epmc = ConnectorHttpClient("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest", HttpPolicy(max_retries=0), transport=httpx.MockTransport(wrap(europepmc)))
    return PubMedConnector(http, epmc, **kwargs), seen


def test_it_satisfies_the_connector_protocol():
    assert isinstance(make(lambda r: {})[0], Connector)


def test_pmid_forms():
    assert pmid("31452104") == pmid("pmid: 31452104") == pmid("https://pubmed.ncbi.nlm.nih.gov/31452104/") == "31452104"
    for bad in ("", "abc", "10.1000/x", "1" * 10):
        with pytest.raises(ConnectorError):
            pmid(bad)


def test_article_normalisation():
    (record,), skipped = parse_articles(feed(article(7, pmc="PMC123")))
    assert skipped == 0
    assert (record.connector, record.external_id, record.title) == ("pubmed", "7", "Remote work and wellbeing")
    assert record.doi == "10.1000/p7" and record.year == 2021 and record.venue == "J Occup Health"
    assert record.authors == ("Ada Lovelace", "The Remote Work Group")
    assert record.abstract == "BACKGROUND: Why it matters. RESULTS: It helps."
    assert record.url == "https://pubmed.ncbi.nlm.nih.gov/7/"
    assert record.source_ids == {"pubmed": "7", "pmcid": "PMC123"} and record.oa_url.endswith("/PMC123/")
    assert record.quality_flags == ()


def test_flags_and_untitled_records():
    records, skipped = parse_articles(feed(
        article(1, types=("Journal Article", "Retracted Publication"), abstract=False, doi="not-a-doi"),
        article(2, types=("Preprint",)),
        article(3, title=""),
    ))
    assert skipped == 1
    assert records[0].quality_flags == ("invalid_doi", "retracted", "no_abstract") and records[0].doi is None
    assert records[1].quality_flags == ("preprint",)


def test_medline_date_gives_the_year():
    xml = feed(article(1).replace("<Year>2021</Year>", "<MedlineDate>2019 Jan-Feb</MedlineDate>"))
    assert parse_articles(xml)[0][0].year == 2019


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE x [<!ENTITY a "aaaa">]><PubmedArticleSet/>',
        '<!DOCTYPE PubmedArticleSet PUBLIC "x" "y" [<!ENTITY a "b">]><PubmedArticleSet/>',
        "<PubmedArticleSet><!ENTITY a 'b'></PubmedArticleSet>",
    ],
)
def test_entity_declarations_are_refused(xml):
    with pytest.raises(ConnectorError, match="DOCTYPE or ENTITY"):
        parse_articles(xml)


def test_invalid_xml_fails_loudly():
    with pytest.raises(ConnectorError, match="not valid XML"):
        parse_articles("<PubmedArticleSet>")


def test_search_pages_through_esearch_then_fetches_in_order():
    def eutils(request):
        if request.url.path.endswith("esearch.fcgi"):
            return {"esearchresult": {"count": "3", "idlist": ["2", "1"]}}
        return feed(article(1), article(2))

    conn, seen = make(eutils, contact_email="lab@example.org", api_key=SecretStr("k123"))
    page = conn.search(SearchRequest(query='"remote work"[tiab]', limit=2, filters={"year_from": 2015}))
    assert [r.external_id for r in page.records] == ["2", "1"]  # PubMed's relevance order, not the XML's
    assert page.total == 3 and page.next_cursor == "2"
    params = seen[0].url.params
    assert params["term"] == '"remote work"[tiab]' and params["retmax"] == "2" and params["retstart"] == "0"
    assert params["mindate"] == "2015" and params["datetype"] == "pdat"
    assert params["tool"] == "research-main" and params["email"] == "lab@example.org" and params["api_key"] == "k123"
    assert seen[1].url.params["id"] == "2,1"


def test_last_page_has_no_cursor_and_empty_search_skips_efetch():
    conn, seen = make(lambda r: {"esearchresult": {"count": "0", "idlist": []}})
    page = conn.search(SearchRequest(query="x"))
    assert page.records == () and page.next_cursor is None and len(seen) == 1


def test_the_esearch_window_is_reported_not_hidden():
    def eutils(request):
        if request.url.path.endswith("esearch.fcgi"):
            return {"esearchresult": {"count": "50000", "idlist": [str(i) for i in range(1, 11)]}}
        return feed(*(article(i) for i in range(1, 11)))

    conn, _ = make(eutils)
    page = conn.search(SearchRequest(query="x", limit=10, cursor=str(ESEARCH_WINDOW - 10)))
    assert page.next_cursor is None and conn.last_search_capped is True
    with pytest.raises(ConnectorError, match="9,999"):
        conn.search(SearchRequest(query="x", cursor=str(ESEARCH_WINDOW)))


def test_search_errors_and_bad_filters():
    conn, _ = make(lambda r: {"esearchresult": {"ERROR": "Invalid query", "idlist": []}})
    with pytest.raises(ConnectorError, match="rejected"):
        conn.search(SearchRequest(query="x"))
    with pytest.raises(ConnectorError, match="Unsupported"):
        conn.search(SearchRequest(query="x", filters={"is_oa": True}))
    with pytest.raises(ConnectorError, match="four-digit"):
        conn.search(SearchRequest(query="x", filters={"year_to": "20x0"}))
    with pytest.raises(ConnectorError):
        conn.search(SearchRequest(query="x", cursor="abc"))


def test_get_by_id_and_missing_record():
    conn, _ = make(lambda r: feed(article(5)) if r.url.params["id"] == "5" else feed())
    assert conn.get_by_id("5").title == "Remote work and wellbeing"
    assert conn.get_by_id("6") is None


def test_get_by_doi_needs_exactly_one_matching_record():
    hits = {"10.1000/p5": ["5"], "10.1000/two": ["5", "6"]}

    def eutils(request):
        if request.url.path.endswith("esearch.fcgi"):
            doi = request.url.params["term"].split('"')[1]
            return {"esearchresult": {"count": "1", "idlist": hits.get(doi, [])}}
        return feed(article(5))

    conn, seen = make(eutils)
    assert conn.get_by_doi("https://doi.org/10.1000/P5").external_id == "5"
    assert seen[0].url.params["term"] == '"10.1000/p5"[doi]'
    assert conn.get_by_doi("10.1000/two") is None  # ambiguous
    assert conn.get_by_doi("10.1000/none") is None
    with pytest.raises(ConnectorError):
        conn.get_by_doi("nope")


def _refs(n_items, total=None, source="MED"):
    items = [{"id": str(100 + i), "source": source, "title": f"Reference {i}", "authorString": "Smith J, Doe A.", "pubYear": "2010", "doi": f"10.1000/r{i}", "journalAbbreviation": "Lancet"} for i in range(n_items)]
    return {"hitCount": total if total is not None else n_items, "referenceList": {"reference": items}}


def test_references_come_from_europe_pmc_and_non_pubmed_entries_are_counted():
    reply = _refs(2)
    reply["referenceList"]["reference"] += [{"source": "AGR", "id": "X1", "title": "Not in PubMed"}, {"title": "Unmatched citation text"}]
    conn, seen = make(lambda r: {}, lambda r: reply)
    records = conn.get_references("9")
    assert [r.external_id for r in records] == ["100", "101"]
    assert records[0].authors == ("Smith J", "Doe A") and records[0].year == 2010 and records[0].doi == "10.1000/r0" and records[0].venue == "Lancet"
    assert conn.last_unresolved_related == 2 and conn.last_related_truncated is False
    assert seen[0].url.path.endswith("/MED/9/references")


def test_citations_page_and_respect_the_cap():
    pages = []

    def epmc(request):
        page = int(request.url.params["page"])
        pages.append(page)
        items = [{"id": str(page * 1000 + i), "source": "MED", "title": f"Citing {i}"} for i in range(1000 if page == 1 else 5)]
        return {"hitCount": 1005, "citationList": {"citation": items}}

    conn, _ = make(lambda r: {}, epmc)
    assert len(conn.get_citations("9")) == 200 and conn.last_related_truncated is True and pages == [1]
    conn.max_related = 5000
    assert len(conn.get_citations("9")) == 1005 and conn.last_related_truncated is False


def test_unknown_record_at_europe_pmc_fails_loudly():
    conn, _ = make(lambda r: {}, lambda r: httpx.Response(404, json={}))
    with pytest.raises(ConnectorError, match="no PubMed record"):
        conn.get_references("9")


def test_full_text_is_not_offered():
    with pytest.raises(NotSupportedError):
        make(lambda r: {})[0].get_fulltext("9")
