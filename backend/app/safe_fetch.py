"""Fetch a PDF from a URL a third party gave us (plan M2.7.1), without letting that URL reach inside.

Open-access PDF links come from Unpaywall, which got them from publishers and repositories, so the
URL is untrusted input. `fetch_pdf(url)` therefore:
  - accepts only http(s) on the default ports (80/443), with no user:password part;
  - resolves the host and refuses it unless *every* address is public (no loopback, private,
    link-local, carrier-grade NAT, multicast or reserved ranges), so a link can't reach the
    database, the cloud metadata endpoint or anything else on the internal network;
  - follows at most `MAX_REDIRECTS` redirects itself, checking each hop the same way;
  - ignores proxy environment variables (a proxy would make the address check meaningless);
  - streams the body with a byte cap and an overall deadline, and checks the `%PDF-` signature,
    so a landing page served as `application/pdf` is not stored as one.

Known limit: the address is checked, then httpx resolves the name again to connect, so a DNS
server that answers differently the second time (DNS rebinding) can slip past. Plan task M2.7.5
tracks pinning the connection to the checked address.

`FetchRefused` means retrying won't help (blocked address, not a PDF, too large, 4xx);
`FetchFailed` means it might (network error, timeout, 5xx, 429).
"""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS = 5
ALLOWED_PORTS = {"http": 80, "https": 443}
USER_AGENT = "research-main/0.1 (open-access full-text fetch)"
PDF_SIGNATURE = b"%PDF-"

Resolver = Callable[[str, int], list[str]]


class FetchError(Exception):
    """Base class: the PDF could not be fetched."""


class FetchRefused(FetchError):
    """The URL or its reply is not acceptable; retrying won't change that."""


class FetchFailed(FetchError):
    """A temporary failure (network, timeout, server error); a retry may succeed."""


def resolve_host(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchFailed(f"Could not resolve {host}") from exc
    return [info[4][0] for info in infos]


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolver: Resolver = resolve_host) -> None:
    """Raise `FetchRefused` unless `url` is http(s) on a default port and its host is public."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchRefused("The PDF link is not a valid URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_PORTS:
        raise FetchRefused("The PDF link must use http or https")
    if parts.username is not None or parts.password is not None:
        raise FetchRefused("The PDF link must not contain a user name or password")
    host = parts.hostname
    if not host:
        raise FetchRefused("The PDF link has no host")
    if port is not None and port != ALLOWED_PORTS[scheme]:
        raise FetchRefused("The PDF link uses a non-standard port")
    addresses = resolver(host, port or ALLOWED_PORTS[scheme])
    if not addresses:
        raise FetchFailed(f"Could not resolve {host}")
    if not all(is_public_address(a) for a in addresses):
        raise FetchRefused("The PDF link points to a private or reserved network address")


def fetch_pdf(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    resolver: Resolver = resolve_host,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str]:
    """Download a PDF. Returns `(bytes, final_url)`; raises `FetchRefused` / `FetchFailed`."""
    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(
        transport=transport, timeout=timeout_seconds, follow_redirects=False, trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"},
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, resolver)
            try:
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchRefused("The PDF host sent a redirect without a location")
                        current = urljoin(current, location)
                        continue
                    _check_status(response.status_code)
                    return _read_capped(response, max_bytes, deadline), current
            except httpx.TimeoutException as exc:
                raise FetchFailed("The PDF host timed out") from exc
            except httpx.TransportError as exc:
                raise FetchFailed("Could not connect to the PDF host") from exc
        raise FetchRefused(f"The PDF link redirected more than {MAX_REDIRECTS} times")


def _check_status(code: int) -> None:
    if code == 429 or code >= 500:
        raise FetchFailed(f"The PDF host answered HTTP {code}")
    if code != 200:
        raise FetchRefused(f"The PDF host answered HTTP {code}")


def _read_capped(response: httpx.Response, max_bytes: int, deadline: float) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise FetchRefused(f"The PDF is larger than the {max_bytes}-byte limit")
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise FetchRefused(f"The PDF is larger than the {max_bytes}-byte limit")
        if time.monotonic() > deadline:
            raise FetchFailed("The PDF download took too long")
    if not body.startswith(PDF_SIGNATURE):
        raise FetchRefused("The PDF link did not return a PDF")
    return bytes(body)
