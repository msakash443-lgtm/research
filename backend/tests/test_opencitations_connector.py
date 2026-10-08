"""Plan M1.4.6: OpenCitations connector (Index v2 + Meta v1). Offline only."""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.opencitations import OpenCitationsConnector, oc_id, to_record


def meta(doi=None, omid="br/0601", title="A cited work", **over):
    ids = " ".join(x for x in (f"doi:{doi}" if doi else "", f"omid:{omid}", "openalex:W42", "pmid:77") if x)
    base = {
        "id": ids,
        "title": title,
        "author": "Peroni, Silvio [orcid:0000-0003-0530-4305 omid:ra/1]; Shotton, David [omid:ra/2]; WHO Consortium [omid:ra/3]",
        "pub_date": "2009-05-01",
        "venue": "Journal of Testing [issn:1234-5678 omid:br/09]",
        "type": "journal article",
    }
    base.update(over)
    return base


def make(handler, **kwargs):
    seen = []

    def inner(request):
        seen.append(request)
        result = handler(request)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    http = ConnectorHttpClient("opencitations", "https://api.opencitations.net", HttpPolicy(max_retries=0), transport=httpx.MockTransport(inner))
    return OpenCitationsConnector(http, **kwargs), seen


def test_it_satisfies_the_connector_protocol():
    assert isinstance(make(lambda r: [])[0], Connector)


def test_token_is_sent_as_authorization_when_configured():
    assert OpenCitationsConnector(access_token=SecretStr("tok"))._http._client.headers["authorization"] == "tok"
    assert "authorization" not in OpenCitationsConnector()._http._client.headers


def test_ids():
    assert oc_id("https://doi.org/10.1000/ABC") == oc_id("doi:10.1000/abc") == "10.1000/abc"
    assert oc_id("omid:br/0612345") == "omid:br/0612345"
    for bad in ("", "W123", "omid:ra/061"):
        with pytest.raises(ConnectorError):
            oc_id(bad)


def test_meta_normalisation():
    r = to_record(meta(doi="10.1000/X"))
    assert (r.connector, r.external_id, r.doi, r.title, r.year) == ("opencitations", "10.1000/x", "10.1000/x", "A cited work", 2009)
    assert r.authors == ("Silvio Peroni", "David Shotton", "WHO Consortium")
    assert r.venue == "Journal of Testing" and r.url == "https://doi.org/10.1000/x"
    assert r.source_ids == {"opencitations": "10.1000/x", "openalex": "W42", "pmid": "77"}
    assert r.quality_flags == ("no_abstract",)
    no_doi = to_record(meta(omid="br/0699"))
    assert no_doi.external_id == "omid:br/0699" and no_doi.doi is None and no_doi.url is None
    assert to_record(meta(doi="10.1000/x", title="")) is None


def test_get_by_id_uses_meta():
    conn, seen = make(lambda r: [meta(doi="10.1000/x")] if "10.1000/x" in r.url.path else [])
    assert conn.get_by_id("10.1000/X").title == "A cited work"
    assert seen[0].url.path == "/meta/v1/metadata/doi:10.1000/x"
    assert conn.get_by_id("10.1000/none") is None


def index_rows(side, ids):
    other = "citing" if side == "cited" else "cited"
    return [{"oci": f"0{i}", other: "omid:br/0600 doi:10.1000/seed", side: ident} for i, ident in enumerate(ids)]


def test_references_resolve_linked_works_through_meta_and_count_the_rest():
    rows = index_rows("cited", ["omid:br/0601 doi:10.1000/a", "omid:br/0602", "doi:10.1000/a", "pmid:5", "omid:br/0603 doi:10.1000/c"])

    def handler(request):
        path = request.url.path
        if path.startswith("/index/v2/references/"):
            return rows
        found = []
        if "doi:10.1000/a" in path:
            found.append(meta(doi="10.1000/a", omid="br/0601", title="Work A"))
        if "omid:br/0602" in path:
            found.append(meta(doi="10.1000/b", omid="br/0602", title="Work B, DOI known to Meta only"))
        return found  # 10.1000/c: Meta has nothing

    conn, seen = make(handler)
    records = conn.get_references("10.1000/seed")
    assert [r.title for r in records] == ["Work A", "Work B, DOI known to Meta only"]
    assert records[1].external_id == "10.1000/b"
    assert conn.last_unresolved_related == 2  # the pmid-only row, and the work Meta didn't know
    assert seen[0].url.path == "/index/v2/references/doi:10.1000/seed"
    assert seen[1].url.path == "/meta/v1/metadata/doi:10.1000/a__omid:br/0602__doi:10.1000/c"


def test_citations_read_the_citing_side_and_respect_the_cap():
    rows = index_rows("citing", [f"doi:10.1000/c{i}" for i in range(30)])

    def handler(request):
        if request.url.path.startswith("/index/v2/citations/"):
            return rows
        dois = [part.removeprefix("doi:") for part in request.url.path.removeprefix("/meta/v1/metadata/").split("__")]
        return [meta(doi=d, omid=f"br/09{n}", title=f"Citing {d}") for n, d in enumerate(dois)]

    conn, seen = make(handler, max_related=25)
    records = conn.get_citations("10.1000/seed")
    assert len(records) == 25 and conn.last_related_truncated is True
    assert len([s for s in seen if s.url.path.startswith("/meta/")]) == 2  # batches of 20


def test_unknown_work_fails_loudly_never_an_empty_list():
    conn, _ = make(lambda r: httpx.Response(404, json={}))
    with pytest.raises(ConnectorError, match="no work"):
        conn.get_references("10.1000/x")


def test_unexpected_replies_fail_loudly():
    conn, _ = make(lambda r: {"not": "a list"})
    with pytest.raises(ConnectorError):
        conn.get_citations("10.1000/x")
    with pytest.raises(ConnectorError):
        conn.get_by_id("10.1000/x")


def test_search_and_full_text_are_not_offered():
    conn, _ = make(lambda r: [])
    with pytest.raises(NotSupportedError):
        conn.search(SearchRequest(query="x"))
    with pytest.raises(NotSupportedError):
        conn.get_fulltext("10.1000/x")
