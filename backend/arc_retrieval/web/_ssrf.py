"""SSRF validation for URLs fetched by the web layer."""

from __future__ import annotations

import http.client
import ipaddress
import socket
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
    getproxies,
    proxy_bypass,
)

# Redirect hops followed by ``safe_urlopen`` (urllib's own default is 10).
MAX_REDIRECTS = 5


def _is_blocked_addr(addr: ipaddress._BaseAddress) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )


def check_url_ssrf(url: str) -> str | None:
    """Return an error message if *url* targets a private/internal host.

    Validates scheme (http/https only) and resolves the hostname to check
    *every* DNS record against the RFC 1918, loopback, link-local, reserved,
    unspecified, and multicast ranges. Blocking only the first record is
    insufficient: a malicious domain may return both a public and a private
    address, and the HTTP client may connect to any of them.

    Returns ``None`` if the URL is safe to fetch.
    """
    if "\\" in url:
        return "Blocked URL containing backslash (potential SSRF bypass)"
    if "@" in url.split("//", 1)[-1].split("/", 1)[0]:
        return "Blocked URL containing userinfo (potential SSRF bypass)"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported URL scheme: {parsed.scheme}"
    hostname = parsed.hostname or ""
    if not hostname:
        return "URL has no hostname"
    # Try parsing hostname as a literal IP address first
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # It's a domain name — resolve and check *all* records, since
        # getaddrinfo may return a mix of public and private addresses
        # and the HTTP client is free to connect to any of them.
        try:
            info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError):
            # Can't resolve — let the actual request fail naturally
            return None
        for record in info:
            try:
                resolved = ipaddress.ip_address(record[4][0])
            except (ValueError, IndexError):
                continue
            if _is_blocked_addr(resolved):
                return f"Blocked internal/private URL: {hostname} → {resolved}"
        return None
    if _is_blocked_addr(addr):
        return f"Blocked internal/private URL: {hostname}"
    return None


class ResponseTooLarge(Exception):
    """A response body exceeded the caller's byte cap."""


class _SSRFRedirectHandler(HTTPRedirectHandler):
    """Re-runs ``check_url_ssrf`` on every redirect target.

    urllib follows 30x responses on its own, so a public URL that redirects
    to e.g. ``http://169.254.169.254/`` would otherwise be fetched after
    passing the initial check.
    """

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        err = check_url_ssrf(newurl)
        if err:
            raise HTTPError(newurl, code, f"Redirect blocked: {err}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SSRFBlocked(OSError):
    """The address a connection was about to use is private/internal."""


def resolve_and_check(host: str, port: int) -> list[tuple[Any, ...]]:
    """Resolve *host* once and return its ``getaddrinfo`` records, all checked.

    Raises ``SSRFBlocked`` if any record is a blocked address. The caller must
    connect to one of the returned addresses (never re-resolve), so a DNS
    answer that changes between the check and the connect can't redirect the
    request to an internal host (DNS rebinding).
    """
    infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"Could not resolve {host}")
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            raise SSRFBlocked(f"Unusable address for {host}: {info[4]!r}") from None
        if _is_blocked_addr(addr):
            raise SSRFBlocked(f"Blocked internal/private address: {host} → {addr}")
    return infos


def _pinned_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *args, **kwargs):  # noqa: ANN001
    """``socket.create_connection`` that only connects to addresses ``resolve_and_check`` approved."""
    host, port = address[0], address[1]
    last_err: OSError | None = None
    for family, socktype, proto, _canon, sockaddr in resolve_and_check(host, port):
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_err = exc
            sock.close()
    raise last_err or OSError(f"Could not connect to {host}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    # TLS still verifies the certificate against the hostname (SNI uses self.host).
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _pinned_create_connection


class _PinnedHTTPHandler(HTTPHandler):
    def http_open(self, req):  # type: ignore[override]
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):  # type: ignore[override]
        kwargs: dict[str, Any] = {"context": self._context}
        if hasattr(self, "_check_hostname"):  # Python < 3.12
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(_PinnedHTTPSConnection, req, **kwargs)


# Without a proxy, every connection (including each redirect hop) goes to an
# address checked at connect time. Through a proxy the proxy resolves the
# target, so pinning can't apply; the URL checks above still do.
_opener = build_opener(_SSRFRedirectHandler, _PinnedHTTPHandler, _PinnedHTTPSHandler)
_proxy_opener = build_opener(_SSRFRedirectHandler)


def _uses_proxy(url: str) -> bool:
    proxies = getproxies()
    scheme = urlparse(url).scheme
    if scheme not in proxies:
        return False
    return not proxy_bypass(urlparse(url).hostname or "")


def safe_urlopen(req: Request | str, *, timeout: float) -> Any:
    """``urlopen`` that checks the URL and every redirect hop for SSRF.

    Without a proxy the connection is also pinned to an address checked at
    connect time (``resolve_and_check``), which closes the DNS-rebinding gap
    between ``check_url_ssrf`` and the connect.

    Raises ``ValueError`` if the initial URL is blocked, ``HTTPError`` if a
    redirect target is blocked or there are more than ``MAX_REDIRECTS`` hops,
    and ``URLError`` (reason ``SSRFBlocked``) if the address resolved at
    connect time is blocked.
    """
    url = req.full_url if isinstance(req, Request) else req
    err = check_url_ssrf(url)
    if err:
        raise ValueError(err)
    opener = _proxy_opener if _uses_proxy(url) else _opener
    return opener.open(req, timeout=timeout)


def read_capped(resp: Any, max_bytes: int) -> bytes:
    """Read a response body, raising ``ResponseTooLarge`` past *max_bytes*.

    A declared ``Content-Length`` over the cap is refused before reading; the
    body itself is read at most ``max_bytes + 1`` bytes, so a missing or false
    header can't make us buffer more than that.
    """
    declared = resp.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_n = int(declared)
        except ValueError:
            declared_n = None
        if declared_n is not None and declared_n > max_bytes:
            raise ResponseTooLarge(
                f"Response too large: Content-Length {declared_n} > {max_bytes} bytes"
            )
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ResponseTooLarge(f"Response too large: over {max_bytes} bytes")
    return data
