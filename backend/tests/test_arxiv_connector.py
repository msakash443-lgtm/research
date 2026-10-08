"""arXiv connector: offline, against canned Atom replies (plan M1.4.4)."""
import httpx
import pytest

from app.connectors.arxiv import ArxivConnector, normalise_id, parse_feed
from app.connectors.base import ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>42</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2101.00001v2</id>
    <published>2021-01-01T10:00:00Z</published>
    <title>Remote work
      and productivity</title>
    <summary>  We study remote work.  </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Grace Hopper</name></author>
    <arxiv:doi>10.1234/RW.2021</arxiv:doi>
    <arxiv:journal_ref>J. Work 3 (2021)</arxiv:journal_ref>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v1</id>
    <published>1999-01-05T00:00:00Z</published>
    <title>An old-style paper</title>
    <summary></summary>
    <author><name>A. Author</name></author>
  </entry>
  <entry><id>http://arxiv.org/abs/2102.00002v1</id><title></title></entry>
</feed>"""

ERROR_FEED = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title><summary>incorrect id format</summary></entry></feed>"""


def connector(body=FEED, status=200):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, text=body)

    http = ConnectorHttpClient("arxiv", "https://export.arxiv.org/api", HttpPolicy(), transport=httpx.MockTransport(respond))
    return ArxivConnector(http=http), seen


def test_feed_entries_become_normalised_preprint_records():
    records, total = parse_feed(FEED)
    assert total == 42 and [r.external_id for r in records] == ["2101.00001", "hep-th/9901001"]  # untitled entry skipped
    first = records[0]
    assert first.title == "Remote work and productivity" and first.authors == ("Ada Lovelace", "Grace Hopper")
    assert first.year == 2021 and first.doi == "10.1234/rw.2021" and first.venue == "J. Work 3 (2021)"
    assert first.url == "https://arxiv.org/abs/2101.00001" and first.oa_url == "https://arxiv.org/pdf/2101.00001"
    assert "preprint" in first.quality_flags and "no_abstract" not in first.quality_flags
    assert "no_abstract" in records[1].quality_flags and records[1].venue == "arXiv"


@pytest.mark.parametrize("given,expected", [
    ("2101.00001v3", "2101.00001"), ("arXiv:2101.00001", "2101.00001"), ("https://arxiv.org/abs/2101.00001v1", "2101.00001"),
    ("https://arxiv.org/pdf/2101.00001.pdf", "2101.00001"), ("hep-th/9901001v2", "hep-th/9901001"), ("math.GT/0309136", "math.GT/0309136"),
])
def test_ids_in_every_common_shape_are_normalised(given, expected):
    assert normalise_id(given) == expected


@pytest.mark.parametrize("bad", ["", "10.1234/abc", "2101", "../../etc/passwd", "all:foo OR all:bar"])
def test_things_that_are_not_arxiv_ids_are_refused(bad):
    with pytest.raises(ConnectorError):
        normalise_id(bad)


def test_search_sends_the_query_and_pages_with_a_cursor():
    c, seen = connector()
    page = c.search(SearchRequest(query='all:"remote work"', limit=2))
    params = seen[0].url.params
    assert params["search_query"] == 'all:"remote work"' and params["start"] == "0" and params["max_results"] == "2"
    assert page.total == 42 and page.next_cursor == "2" and len(page.records) == 2
    c.search(SearchRequest(query="x", limit=2, cursor="2"))
    assert seen[1].url.params["start"] == "2"


def test_the_last_page_has_no_cursor():
    c, _ = connector(FEED.replace("<opensearch:totalResults>42", "<opensearch:totalResults>2"))
    assert c.search(SearchRequest(query="x")).next_cursor is None


def test_get_by_id_normalises_and_returns_none_when_absent():
    c, seen = connector()
    assert c.get_by_id("arXiv:2101.00001v2").external_id == "2101.00001"
    assert seen[0].url.params["id_list"] == "2101.00001"
    empty, _ = connector('<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    assert empty.get_by_id("2101.00001") is None


def test_an_error_feed_fails_loudly():
    c, _ = connector(ERROR_FEED)
    with pytest.raises(ConnectorError, match="rejected"):
        c.search(SearchRequest(query="bad"))


def test_a_reply_with_an_entity_declaration_is_refused_before_parsing():
    bomb = '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><feed xmlns="http://www.w3.org/2005/Atom">&b;</feed>'
    with pytest.raises(ConnectorError, match="DOCTYPE"):
        parse_feed(bomb)


def test_invalid_xml_and_bad_cursors_fail_loudly():
    with pytest.raises(ConnectorError):
        parse_feed("<feed><entry>")
    c, _ = connector()
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x", cursor="abc"))


def test_a_bad_doi_in_a_feed_is_dropped_not_stored():
    records, _ = parse_feed(FEED.replace("10.1234/RW.2021", "not a doi"))
    assert records[0].doi is None


def test_citations_references_and_fulltext_are_not_offered():
    c, _ = connector()
    for call in (c.get_citations, c.get_references, c.get_fulltext):
        with pytest.raises(NotSupportedError):
            call("2101.00001")
