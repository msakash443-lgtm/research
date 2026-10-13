"""DNS-rebinding defence (plan M2.7.5): the connection goes to the address that was checked, nowhere else.

`httpx.MockTransport` never dials anything, so these tests look at the request httpx would send:
its URL host is the address it connects to, and the `Host` header and `sni_hostname` extension are
the name the server and the TLS certificate check see.
"""

import httpx
import pytest

from app.safe_fetch import FetchRefused, fetch_pdf

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"
PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "93.184.216.35"
PRIVATE = "10.0.0.1"
PUBLIC_V6 = "2606:4700:4700::1111"


def answers(first: dict[str, list[str]], later: dict[str, list[str]] | None = None):
    """Resolver answering `first[name]` on a name's first lookup and `later[name]` on every lookup after.

    Returns `(resolver, lookups)`; `lookups` records each name the resolver was asked about.
    """
    seen: set[str] = set()
    lookups: list[str] = []

    def resolve(host, port):
        lookups.append(host)
        table = later if later is not None and host in seen else first
        seen.add(host)
        return table.get(host, [PUBLIC])

    return resolve, lookups


def serve(requests: list[httpx.Request], reply=None):
    def handler(request):
        requests.append(request)
        if reply is not None:
            return reply(request)
        return httpx.Response(200, content=PDF)

    return httpx.MockTransport(handler)


def fetch(url, resolver, transport):
    return fetch_pdf(url, max_bytes=1000, timeout_seconds=5, resolver=resolver, transport=transport)


def test_a_name_that_rebinds_to_a_private_address_is_dialled_only_at_the_checked_address():
    resolver, lookups = answers({"rebind.example": [PUBLIC]}, later={"rebind.example": [PRIVATE]})
    requests = []

    data, final = fetch("https://rebind.example/a.pdf", resolver, serve(requests))

    assert data == PDF and final == "https://rebind.example/a.pdf"
    assert [r.url.host for r in requests] == [PUBLIC]
    assert lookups == ["rebind.example"]


def test_https_dials_the_address_but_keeps_the_name_for_host_header_and_tls():
    resolver, _ = answers({"example.org": [PUBLIC]})
    requests = []

    fetch("https://example.org/a.pdf", resolver, serve(requests))

    (request,) = requests
    assert request.url.scheme == "https" and request.url.host == PUBLIC
    assert request.headers["host"] == "example.org"
    assert request.extensions["sni_hostname"] == "example.org"


def test_an_ipv6_address_is_dialled_with_the_name_in_host_header():
    resolver, _ = answers({"v6.example": [PUBLIC_V6]})
    requests = []

    fetch("https://v6.example/a.pdf", resolver, serve(requests))

    (request,) = requests
    assert request.url.host == PUBLIC_V6
    assert request.headers["host"] == "v6.example"
    assert request.extensions["sni_hostname"] == "v6.example"


def test_each_redirect_hop_is_checked_and_dialled_at_its_own_checked_address():
    resolver, _ = answers({"example.org": [PUBLIC], "cdn.example.org": [OTHER_PUBLIC]})
    requests = []

    def reply(request):
        if request.headers["host"] == "example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/files/a.pdf"})
        return httpx.Response(200, content=PDF)

    data, final = fetch("https://example.org/a.pdf", resolver, serve(requests, reply))

    assert data == PDF and final == "https://cdn.example.org/files/a.pdf"
    assert [r.url.host for r in requests] == [PUBLIC, OTHER_PUBLIC]
    assert [r.headers["host"] for r in requests] == ["example.org", "cdn.example.org"]
    assert [r.extensions["sni_hostname"] for r in requests] == ["example.org", "cdn.example.org"]


def test_a_redirect_to_a_name_with_any_private_address_is_refused_and_never_dialled():
    resolver, _ = answers({"example.org": [PUBLIC], "files.example": [OTHER_PUBLIC, PRIVATE]})
    requests = []

    def reply(request):
        return httpx.Response(302, headers={"location": "https://files.example/a.pdf"})

    with pytest.raises(FetchRefused, match="private"):
        fetch("https://example.org/a.pdf", resolver, serve(requests, reply))

    assert [r.url.host for r in requests] == [PUBLIC]


def test_a_redirect_target_that_rebinds_after_its_check_is_dialled_only_at_the_check():
    resolver, lookups = answers(
        {"example.org": [PUBLIC], "rebind.example": [OTHER_PUBLIC]},
        later={"rebind.example": [PRIVATE]},
    )
    requests = []

    def reply(request):
        if request.headers["host"] == "example.org":
            return httpx.Response(302, headers={"location": "https://rebind.example/a.pdf"})
        return httpx.Response(200, content=PDF)

    data, _ = fetch("https://example.org/a.pdf", resolver, serve(requests, reply))

    assert data == PDF
    assert [r.url.host for r in requests] == [PUBLIC, OTHER_PUBLIC]
    assert lookups == ["example.org", "rebind.example"]
