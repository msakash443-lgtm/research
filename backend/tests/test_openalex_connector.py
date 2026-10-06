import httpx
import pytest

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.openalex import OpenAlexConnector, to_record, work_id


def work(n=1, **over):
    base = {
        "id": f"https://openalex.org/W{n}",
        "display_name": f"Paper {n}",
        "doi": f"https://doi.org/10.1000/P{n}",
        "publication_year": 2020,
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {"display_name": " "}}],
        "primary_location": {"landing_page_url": "https://pub.test/p", "source": {"display_name": "J. Testing"}},
        "open_access": {"oa_url": "https://oa.test/p.pdf"},
        "abstract_inverted_index": {"Hello": [0], "world": [1]},
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345"},
        "type": "article",
    }
    base.update(over)
    return base


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
    http = ConnectorHttpClient(
        "openalex", "https://api.openalex.org", HttpPolicy(max_retries=0), transport=httpx.MockTransport(server)
    )
    return OpenAlexConnector(http, **kwargs), server


def test_it_satisfies_the_connector_protocol():
    assert isinstance(connector(lambda r: {})[0], Connector)


def test_work_normalisation():
    record = to_record(work())
    assert (record.connector, record.external_id, record.title) == ("openalex", "W1", "Paper 1")
    assert record.doi == "10.1000/p1"
    assert record.authors == ("Ada Lovelace",)  # blank author dropped
    assert (record.year, record.venue, record.abstract) == (2020, "J. Testing", "Hello world")
    assert (record.url, record.oa_url) == ("https://pub.test/p", "https://oa.test/p.pdf")
    assert record.source_ids == {"openalex": "W1", "pmid": "12345"}
    assert record.quality_flags == ()


def test_flags_and_degraded_fields():
    record = to_record(work(is_retracted=True, type="preprint", abstract_inverted_index=None, doi="garbage", open_access={}))
    assert set(record.quality_flags) == {"retracted", "preprint", "no_abstract", "invalid_doi"}
    assert record.doi is None and record.oa_url is None


@pytest.mark.parametrize("bad", [{"display_name": None, "title": None}, {"id": "nonsense"}, {"display_name": "  "}])
def test_unrepresentable_works_are_skipped_and_counted(bad):
    assert to_record(work(**bad)) is None
    c, _ = connector(lambda r: {"meta": {"count": 2}, "results": [work(1, **bad), work(2)]})
    page = c.search(SearchRequest(query="x"))
    assert [r.external_id for r in page.records] == ["W2"]
    assert c.skipped_records == 1


def test_abstract_ignores_hostile_positions():
    huge = {"a": [10**9], "b": [-1], "c": ["x"], "ok": [0]}
    assert to_record(work(abstract_inverted_index=huge)).abstract == "ok"


def test_search_sends_query_filters_paging_and_contact():
    c, server = connector(
        lambda r: {"meta": {"count": 42, "next_cursor": "abc"}, "results": [work(1)]}, contact_email=" me@lab.example "
    )
    page = c.search(SearchRequest(query="labour AND women", limit=10, filters={"year_from": 2015, "year_to": 2020, "type": "article", "is_oa": True}))
    params = server.requests[0].url.params
    assert params["search"] == "labour AND women" and params["per-page"] == "10" and params["cursor"] == "*"
    assert params["filter"] == "from_publication_date:2015-01-01,to_publication_date:2020-12-31,type:article,is_oa:true"
    assert params["mailto"] == "me@lab.example"
    assert (page.total, page.next_cursor, len(page.records)) == (42, "abc", 1)

    c.search(SearchRequest(query="x", cursor="abc"))
    assert server.requests[1].url.params["cursor"] == "abc"
    assert "filter" not in server.requests[1].url.params


def test_no_contact_means_no_mailto_and_last_page_has_no_cursor():
    c, server = connector(lambda r: {"meta": {"count": 1, "next_cursor": None}, "results": []})
    page = c.search(SearchRequest(query="x"))
    assert "mailto" not in server.requests[0].url.params
    assert page.next_cursor is None and page.records == ()


@pytest.mark.parametrize(
    "filters",
    [{"language": "en"}, {"year_from": "20x0"}, {"year_from": True}, {"type": "a,b"}, {"type": "Article; drop"}, {"is_oa": "yes"}],
)
def test_bad_or_unknown_filters_fail_before_any_request(filters):
    c, server = connector(lambda r: {})
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x", filters=filters))
    assert server.requests == []


def test_malformed_search_reply_fails_loudly():
    c, _ = connector(lambda r: {"meta": {}, "results": "nope"})
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x"))


@pytest.mark.parametrize("value", ["W1", "w1", "https://openalex.org/W1", " W1 "])
def test_work_id_accepts_known_forms(value):
    assert work_id(value) == "W1"


@pytest.mark.parametrize("value", ["", "1", "W", "W1/../x", "W1?x=1", "https://evil.test/W1", "A123", "W1,W2"])
def test_work_id_refuses_everything_else(value):
    with pytest.raises(ConnectorError):
        work_id(value)


def test_get_by_id_and_missing_record():
    c, server = connector(lambda r: work(7) if r.url.path == "/works/W7" else httpx.Response(404, json={}))
    assert c.get_by_id("https://openalex.org/W7").external_id == "W7"
    assert c.get_by_id("W8") is None  # 404 is "no such record"
    with pytest.raises(ConnectorError):
        c.get_by_id("W1/../../x")
    assert len(server.requests) == 2


def test_get_by_id_server_error_is_not_none():
    c, _ = connector(lambda r: httpx.Response(500, json={}))
    with pytest.raises(ConnectorError):
        c.get_by_id("W1")


def test_get_citations_pages_through_cites_filter():
    pages = {"*": ([work(1), work(2)], "p2"), "p2": ([work(3)], None)}

    def handler(request):
        results, cursor = pages[request.url.params["cursor"]]
        return {"meta": {"next_cursor": cursor}, "results": results}

    c, server = connector(handler)
    assert [r.external_id for r in c.get_citations("W9")] == ["W1", "W2", "W3"]
    assert server.requests[0].url.params["filter"] == "cites:W9"
    assert c.last_related_truncated is False


def test_get_citations_is_capped_and_says_so():
    c, _ = connector(lambda r: {"meta": {"next_cursor": "more"}, "results": [work(i) for i in range(1, 4)]}, max_related=5)
    assert len(c.get_citations("W9")) == 5
    assert c.last_related_truncated is True


def test_get_citations_stops_on_empty_page_even_with_cursor():
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) < 4, "paging did not stop on an empty page"
        return {"meta": {"next_cursor": "loop"}, "results": []}

    c, server = connector(handler)
    assert c.get_citations("W9") == ()
    assert len(server.requests) == 1


def test_get_references_fetches_referenced_works_in_batches():
    refs = [f"https://openalex.org/W{i}" for i in range(1, 121)] + ["https://evil.test/x"]

    def handler(request):
        if request.url.path == "/works/W9":
            return {"id": "https://openalex.org/W9", "referenced_works": refs}
        wanted = request.url.params["filter"].removeprefix("openalex:").split("|")
        return {"meta": {}, "results": [work(int(w[1:])) for w in wanted]}

    c, server = connector(handler, max_related=500)
    records = c.get_references("W9")
    assert len(records) == 120  # the foreign URL was ignored
    assert [len(r.url.params["filter"].split("|")) for r in server.requests[1:]] == [50, 50, 20]
    assert c.last_related_truncated is False


def test_get_references_cap_and_missing_work():
    refs = [f"https://openalex.org/W{i}" for i in range(1, 11)]

    def handler(request):
        if request.url.path == "/works/W9":
            return {"id": "x", "referenced_works": refs}
        wanted = request.url.params["filter"].removeprefix("openalex:").split("|")
        return {"results": [work(int(w[1:])) for w in wanted]}

    c, _ = connector(handler, max_related=4)
    assert len(c.get_references("W9")) == 4 and c.last_related_truncated is True

    gone, _ = connector(lambda r: httpx.Response(404, json={}))
    with pytest.raises(ConnectorError):
        gone.get_references("W9")  # not "this work cites nothing"


def test_fulltext_is_not_supported():
    with pytest.raises(NotSupportedError):
        connector(lambda r: {})[0].get_fulltext("W1")


def test_invalid_max_related():
    with pytest.raises(ValueError):
        OpenAlexConnector(max_related=0)
