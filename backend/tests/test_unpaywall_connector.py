import httpx
import pytest

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.unpaywall import OaLocation, UnpaywallConnector, to_location, to_record

EMAIL = "lab@university.example"


def reply(**over):
    base = {
        "doi": "10.1000/abc",
        "title": "An OA paper",
        "year": 2021,
        "journal_name": "J. Open",
        "doi_url": "https://doi.org/10.1000/abc",
        "is_oa": True,
        "best_oa_location": {
            "url": "https://repo.test/landing",
            "url_for_pdf": "https://repo.test/p.pdf",
            "license": "cc-by",
            "version": "publishedVersion",
            "host_type": "repository",
        },
        "z_authors": [{"given": "Ada", "family": "Lovelace"}, {"raw_author_name": "ACME Group"}, {"given": " "}],
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


def connector(handler, email=EMAIL):
    server = Server(handler)
    http = ConnectorHttpClient("unpaywall", "https://api.unpaywall.test/v2", HttpPolicy(max_retries=0), transport=httpx.MockTransport(server))
    return UnpaywallConnector(http, email), server


def test_it_satisfies_the_connector_protocol():
    assert isinstance(connector(lambda r: {})[0], Connector)


@pytest.mark.parametrize("email", [None, "", "  ", "not-an-email", "a b@c.d", "a@b", "a@b.c&x=1", "a@b.c,d@e.f"])
def test_refuses_to_run_without_a_real_contact_email(email):
    with pytest.raises(ConnectorError, match="CONNECTOR_CONTACT_EMAIL"):
        UnpaywallConnector(None, email)


def test_email_is_sent_on_every_request_and_doi_is_normalised():
    c, server = connector(lambda r: reply())
    c.get_oa_location("https://doi.org/10.1000/ABC")
    request = server.requests[0]
    assert request.url.path == "/v2/10.1000/abc" and request.url.params["email"] == EMAIL


def test_best_location_with_licence_version_and_host():
    c, _ = connector(lambda r: reply())
    assert c.get_oa_location("10.1000/abc") == OaLocation(
        doi="10.1000/abc",
        landing_page_url="https://repo.test/landing",
        pdf_url="https://repo.test/p.pdf",
        license="cc-by",
        version="publishedVersion",
        host_type="repository",
    )


def test_known_doi_with_no_oa_copy_is_none_not_an_error():
    c, _ = connector(lambda r: reply(is_oa=False, best_oa_location=None))
    assert c.get_oa_location("10.1000/abc") is None


def test_unknown_doi_raises_for_location_but_is_none_for_lookup():
    c, _ = connector(lambda r: httpx.Response(404, json={"error": True, "message": "not found"}))
    with pytest.raises(ConnectorError, match="no record"):
        c.get_oa_location("10.1000/zzz")
    assert c.get_by_id("10.1000/zzz") is None


@pytest.mark.parametrize(
    "best",
    [
        {"url": None, "url_for_pdf": None},
        {"url": "javascript:alert(1)", "url_for_pdf": "file:///etc/passwd"},
        "not-a-dict",
    ],
)
def test_locations_without_a_safe_http_url_are_not_returned(best):
    assert to_location(reply(best_oa_location=best), "10.1000/abc") is None


def test_hostile_urls_are_dropped_but_a_good_one_is_kept():
    location = to_location(reply(best_oa_location={"url": "javascript:x", "url_for_pdf": "https://ok.test/p.pdf"}), "10.1000/abc")
    assert location.landing_page_url is None and location.pdf_url == "https://ok.test/p.pdf"


def test_unknown_licence_stays_unknown():
    location = to_location(reply(best_oa_location={"url": "https://r.test/x"}), "10.1000/abc")
    assert location.license is None and location.version is None


def test_record_prefers_pdf_for_oa_url_and_has_no_abstract():
    record = to_record(reply())
    assert (record.external_id, record.doi, record.title, record.year, record.venue) == ("10.1000/abc", "10.1000/abc", "An OA paper", 2021, "J. Open")
    assert record.oa_url == "https://repo.test/p.pdf"
    assert record.authors == ("Ada Lovelace", "ACME Group")
    assert record.quality_flags == ("no_abstract",) and record.abstract is None
    assert to_record(reply(is_oa=False, best_oa_location=None)).oa_url is None


@pytest.mark.parametrize("bad", [{"title": None}, {"title": " "}, {"doi": "nope"}])
def test_unrepresentable_record_is_none_and_lookup_raises(bad):
    assert to_record(reply(**bad)) is None
    c, _ = connector(lambda r: reply(**bad))
    with pytest.raises(ConnectorError):
        c.get_by_id("10.1000/abc")


def test_location_works_even_without_a_title():
    c, _ = connector(lambda r: reply(title=None))
    assert c.get_oa_location("10.1000/abc").pdf_url == "https://repo.test/p.pdf"


def test_bad_dois_fail_before_any_request():
    c, server = connector(lambda r: reply())
    for call in (c.get_oa_location, c.get_by_id):
        for bad in ("", "abc", "W123"):
            with pytest.raises(ConnectorError):
                call(bad)
    assert server.requests == []


def test_hostile_doi_is_encoded_in_the_path():
    c, server = connector(lambda r: httpx.Response(404, json={}))
    c.get_by_id("10.1000/a?x=1#f")
    assert server.requests[0].url.raw_path.startswith(b"/v2/10.1000/a%3Fx%3D1%23f?")


@pytest.mark.parametrize("response", [httpx.Response(500, json={}), httpx.Response(422, json={"error": True}), httpx.Response(200, json=[]), httpx.Response(200, json={"error": True})])
def test_failures_and_malformed_replies_raise(response):
    c, _ = connector(lambda r: response)
    with pytest.raises(ConnectorError):
        c.get_oa_location("10.1000/abc")


def test_unsupported_capabilities():
    c, server = connector(lambda r: {})
    for call in (
        lambda: c.search(SearchRequest(query="x")),
        lambda: c.get_citations("10.1000/abc"),
        lambda: c.get_references("10.1000/abc"),
        lambda: c.get_fulltext("10.1000/abc"),
    ):
        with pytest.raises(NotSupportedError):
            call()
    assert server.requests == []


def test_is_oa_false_wins_over_a_leftover_location():
    assert to_location(reply(is_oa=False), "10.1000/abc") is None
    assert to_record(reply(is_oa=False)).oa_url is None
