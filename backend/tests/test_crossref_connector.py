import httpx
import pytest

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.crossref import CrossrefConnector, doi_id, to_record
from app.connectors.http import ConnectorHttpClient, HttpPolicy


def item(n=1, **over):
    base = {
        "DOI": f"10.1000/P{n}",
        "title": [f"Paper {n}"],
        "author": [{"given": "Ada", "family": "Lovelace"}, {"name": "ACME Consortium"}, {"given": " "}],
        "issued": {"date-parts": [[2020, 5]]},
        "container-title": ["J. Testing"],
        "abstract": "<jats:p>Hello &amp; <jats:italic>welcome</jats:italic></jats:p>",
        "URL": "https://doi.org/10.1000/p1",
        "type": "journal-article",
    }
    base.update(over)
    return base


def ok(message):
    return {"status": "ok", "message": message}


class Server:
    def __init__(self, handler):
        self.requests = []
        self.handler = handler

    def __call__(self, request):
        self.requests.append(request)
        result = self.handler(request)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)


def connector(handler, **kwargs):
    server = Server(handler)
    http = ConnectorHttpClient("crossref", "https://api.crossref.org", HttpPolicy(max_retries=0), transport=httpx.MockTransport(server))
    return CrossrefConnector(http, **kwargs), server


def test_it_satisfies_the_connector_protocol():
    assert isinstance(connector(lambda r: {})[0], Connector)


def test_work_normalisation():
    record = to_record(item())
    assert (record.connector, record.external_id, record.doi) == ("crossref", "10.1000/p1", "10.1000/p1")
    assert record.title == "Paper 1"
    assert record.authors == ("Ada Lovelace", "ACME Consortium")  # name-only kept, empty dropped
    assert (record.year, record.venue) == (2020, "J. Testing")
    assert record.abstract == "Hello & welcome"  # JATS tags stripped, entities decoded
    assert record.source_ids == {"crossref": "10.1000/p1"} and record.oa_url is None
    assert record.quality_flags == ()


def test_flags_and_fallbacks():
    record = to_record(item(type="posted-content", abstract=None, issued={}, published={"date-parts": [[2019]]}))
    assert set(record.quality_flags) == {"preprint", "no_abstract"}
    assert record.year == 2019


def test_leading_abstract_label_and_script_text_are_neutralised():
    record = to_record(item(abstract="<jats:title>Abstract</jats:title><script>alert(1)</script> Findings."))
    assert "<" not in record.abstract and not record.abstract.startswith("Abstract")


@pytest.mark.parametrize("bad", [{"DOI": "nope"}, {"title": []}, {"title": [" "]}, {"DOI": None}])
def test_unrepresentable_items_are_skipped_and_counted(bad):
    assert to_record(item(**bad)) is None
    c, _ = connector(lambda r: ok({"total-results": 2, "items": [item(1, **bad), item(2)]}))
    assert [r.external_id for r in c.search(SearchRequest(query="x")).records] == ["10.1000/p2"]
    assert c.skipped_records == 1


def test_search_sends_query_filters_paging_and_contact():
    c, server = connector(lambda r: ok({"total-results": 99, "next-cursor": "abc", "items": [item(1)]}), contact_email=" me@lab.example ")
    page = c.search(SearchRequest(query="labour", limit=10, filters={"year_from": 2015, "year_to": 2020, "type": "journal-article"}))
    params = server.requests[0].url.params
    assert (params["query"], params["rows"], params["cursor"]) == ("labour", "10", "*")
    assert params["filter"] == "from-pub-date:2015,until-pub-date:2020,type:journal-article"
    assert params["mailto"] == "me@lab.example"
    assert (page.total, page.next_cursor, len(page.records)) == (99, "abc", 1)


def test_no_contact_configured_means_no_mailto():
    c, server = connector(lambda r: ok({"items": []}))
    c.search(SearchRequest(query="x"))
    assert "mailto" not in server.requests[0].url.params


def test_an_empty_page_ends_paging_even_though_crossref_returns_a_cursor():
    c, _ = connector(lambda r: ok({"total-results": 0, "next-cursor": "still-here", "items": []}))
    assert c.search(SearchRequest(query="x", cursor="abc")).next_cursor is None


@pytest.mark.parametrize(
    "filters", [{"language": "en"}, {"year_to": "20x0"}, {"year_from": False}, {"type": "a,b"}, {"type": "Journal Article"}]
)
def test_bad_filters_fail_before_any_request(filters):
    c, server = connector(lambda r: {})
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x", filters=filters))
    assert server.requests == []


@pytest.mark.parametrize("reply", [{"status": "failed", "message": {}}, {"message": "nope"}, [], {"status": "ok", "message": {"items": "x"}}])
def test_malformed_replies_fail_loudly(reply):
    c, _ = connector(lambda r: reply)
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x"))


@pytest.mark.parametrize("value", ["10.1000/ABC", "https://doi.org/10.1000/abc", "doi:10.1000/abc"])
def test_doi_id_normalises(value):
    assert doi_id(value) == "10.1000/abc"


@pytest.mark.parametrize("value", ["", "abc", "10.1/x", "W123"])
def test_doi_id_refuses_non_dois(value):
    with pytest.raises(ConnectorError):
        doi_id(value)


def test_get_by_id_returns_record_none_for_unknown_and_raises_on_failure():
    def handler(request):
        if request.url.path == "/works/10.1000/p1":
            return ok(item(1))
        return httpx.Response(404, text="Resource not found.")

    c, server = connector(handler)
    assert c.get_by_id("https://doi.org/10.1000/P1").title == "Paper 1"
    assert c.get_by_id("10.1000/zzz") is None
    with pytest.raises(ConnectorError):
        c.get_by_id("not a doi")
    assert len(server.requests) == 2

    broken, _ = connector(lambda r: httpx.Response(500, json={}))
    with pytest.raises(ConnectorError):
        broken.get_by_id("10.1000/p1")


def test_get_by_id_encodes_hostile_dois_in_the_path():
    c, server = connector(lambda r: httpx.Response(404, text=""))
    c.get_by_id("10.1000/a?x=1#frag")
    assert server.requests[0].url.raw_path == b"/works/10.1000/a%3Fx%3D1%23frag"


def test_get_references_resolves_dois_in_batches_and_counts_the_rest():
    refs = [{"DOI": f"10.1000/R{i}"} for i in range(1, 121)]
    refs += [{"DOI": "10.1000/R1"}, {"unstructured": "A book"}, {"DOI": "bad"}, {"DOI": "10.1000/with,comma"}, "junk"]

    def handler(request):
        if request.url.path == "/works/10.1000/p9":
            return ok({"DOI": "10.1000/p9", "reference": refs})
        wanted = [p.removeprefix("doi:") for p in request.url.params["filter"].split(",")]
        return ok({"items": [item(int(d.rsplit("r", 1)[1]), DOI=d.upper()) for d in wanted]})

    c, server = connector(handler, max_related=500)
    records = c.get_references("10.1000/P9")
    assert len(records) == 120  # duplicate collapsed
    assert [len(r.url.params["filter"].split(",")) for r in server.requests[1:]] == [50, 50, 20]
    assert c.last_unresolved_references == 4  # no DOI, malformed DOI, comma DOI, non-dict entry
    assert c.last_related_truncated is False


def test_get_references_cap_missing_work_and_no_references():
    refs = [{"DOI": f"10.1000/R{i}"} for i in range(1, 11)]

    def handler(request):
        if request.url.path == "/works/10.1000/p9":
            return ok({"reference": refs})
        wanted = [p.removeprefix("doi:") for p in request.url.params["filter"].split(",")]
        return ok({"items": [item(int(d.rsplit("r", 1)[1]), DOI=d) for d in wanted]})

    c, _ = connector(handler, max_related=4)
    assert len(c.get_references("10.1000/p9")) == 4 and c.last_related_truncated is True

    gone, _ = connector(lambda r: httpx.Response(404, text=""))
    with pytest.raises(ConnectorError):
        gone.get_references("10.1000/p9")  # not "cites nothing"

    none, server = connector(lambda r: ok({"DOI": "10.1000/p9"}))
    assert none.get_references("10.1000/p9") == () and len(server.requests) == 1


def test_citations_and_fulltext_are_not_supported():
    c, server = connector(lambda r: {})
    for call in (lambda: c.get_citations("10.1000/p1"), lambda: c.get_fulltext("10.1000/p1")):
        with pytest.raises(NotSupportedError):
            call()
    assert server.requests == []


def test_invalid_max_related():
    with pytest.raises(ValueError):
        CrossrefConnector(max_related=0)
