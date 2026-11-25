"""The vendored ARC crawler's SSRF guard blocks every non-global address, not only RFC 1918 and loopback."""

import socket

import pytest

from arc_retrieval.web._ssrf import check_url_ssrf


@pytest.mark.parametrize("url", [
    "http://100.100.100.200/latest/meta-data/",
    "http://100.64.0.1/",
    "http://[::ffff:169.254.169.254]/",
    "http://[::ffff:100.100.100.200]/",
    "http://192.0.0.8/",
    "http://198.18.0.1/",
])
def test_arc_refuses_non_global_literal_addresses_including_cgnat_and_mapped_ipv6(url):
    assert check_url_ssrf(url) is not None


def test_arc_refuses_a_hostname_that_resolves_to_a_cgnat_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 0))])

    assert "Blocked" in check_url_ssrf("https://evil.example/path")


def test_arc_allows_a_public_address_literal_and_a_hostname_resolving_to_one(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])

    assert check_url_ssrf("http://93.184.216.34/") is None
    assert check_url_ssrf("https://example.org/") is None
