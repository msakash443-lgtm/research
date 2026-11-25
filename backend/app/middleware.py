from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]

logger = logging.getLogger(__name__)


TOO_LARGE_DETAIL = "Request body is too large."


class RequestBodyTooLarge(HTTPException):
    """Raised from the wrapped `receive` once the bytes received pass the limit. It is an HTTPException so
    FastAPI's body parsing re-raises it (any other error there becomes a 400) and the app answers 413."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=TOO_LARGE_DETAIL)


class ContentLengthLimitMiddleware:
    """Reject obviously oversized request bodies before FastAPI reads them into memory.

    A reverse proxy should enforce the same limit. A declared Content-Length over the limit is refused
    before the route runs; every body is also counted as it is received, so a chunked body or a missing,
    false or unparseable Content-Length is cut off at the same limit (X.112). The manual-upload endpoint
    (M2.7.2) keeps its own streaming cap as well.

    `upload_path_suffix`/`upload_max_bytes` let the raw-body upload routes (the manual full-text upload and
    the voice/photo note captures) use a larger cap than the default, which stays small for every ordinary
    JSON/text request. `upload_path_suffix` is one suffix or a tuple of them.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int,
        *,
        upload_path_suffix: str | tuple[str, ...] | None = None,
        upload_max_bytes: int | None = None,
    ):
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.upload_path_suffix = upload_path_suffix
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] == "http":
            limit = self.max_body_bytes
            if (
                self.upload_path_suffix
                and scope.get("method") == "POST"
                and scope.get("path", "").endswith(self.upload_path_suffix)
            ):
                limit = self.upload_max_bytes or limit
            headers = dict(scope.get("headers", []))
            raw_length = headers.get(b"content-length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    content_length = 0
                if content_length > limit:
                    await _send_too_large(send)
                    return
            received = 0
            response_started = False

            async def limited_receive() -> dict:
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise RequestBodyTooLarge()
                return message

            async def tracking_send(message: dict) -> None:
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)

            try:
                await self.app(scope, limited_receive, tracking_send)
            except RequestBodyTooLarge:
                # Only reached when nothing inside turned it into a response (e.g. a non-FastAPI app).
                if response_started:
                    raise
                await _send_too_large(send)
            return
        await self.app(scope, receive, send)


RATE_LIMITED_DETAIL = "Too many requests. Try again shortly."
AUTH_RATE_LIMITED_PATHS = ("/api/auth/login", "/api/auth/callback", "/api/auth/development/login")


class FixedWindowCounter:
    """Requests per key in the current window, in this process only. `now` is injectable for tests."""

    def __init__(self, window_seconds: float = 60.0, now: Callable[[], float] = time.monotonic):
        self.window_seconds = window_seconds
        self.now = now
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int) -> float | None:
        """Count one request. None if it is allowed, else the seconds until the window resets."""
        now = self.now()
        with self._lock:
            if len(self._windows) > 10_000:  # forget windows that have ended, so memory stays bounded
                self._windows = {k: v for k, v in self._windows.items() if now - v[0] < self.window_seconds}
            started, count = self._windows.get((bucket, key), (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0
            if count >= limit:
                return self.window_seconds - (now - started)
            self._windows[(bucket, key)] = (started, count + 1)
            return None

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


class RateLimitMiddleware:
    """Per-minute caps on sign-in and upload requests (X.143), answered with 429 and `Retry-After`.

    Counts live in this process (`FixedWindowCounter`), so with several web processes each enforces
    its own cap; the reverse proxy should still limit the edge (docs/DEPLOYMENT.md). It sits inside the
    session middleware, so a signed-in caller is counted by user id and anyone else by client address
    (behind a proxy that address is only right when `FORWARDED_ALLOW_IPS` is set). Limits are read from
    `Settings` per request, so they can be changed without rebuilding the app.
    """

    def __init__(self, app: ASGIApp, *, upload_path_suffix: tuple[str, ...], counter: FixedWindowCounter):
        self.app = app
        self.upload_path_suffix = upload_path_suffix
        self.counter = counter

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] == "http":
            from app.config import get_settings  # imported here: app.config imports nothing from this module

            settings = get_settings()
            path = scope.get("path", "")
            bucket, limit = None, 0
            if path in AUTH_RATE_LIMITED_PATHS:
                bucket, limit = "auth", settings.rate_limit_auth_per_minute
            elif scope.get("method") == "POST" and path.endswith(self.upload_path_suffix):
                bucket, limit = "upload", settings.rate_limit_upload_per_minute
            if bucket and settings.rate_limit_enabled:
                retry_after = self.counter.hit(bucket, _caller_key(scope), limit)
                if retry_after is not None:
                    logger.warning("Rate limit refused %s %s (%s caller)", scope.get("method"), path, _caller_key(scope).split(":", 1)[0])
                    await _send_rate_limited(send, retry_after)
                    return
        await self.app(scope, receive, send)


def _caller_key(scope: dict) -> str:
    user_id = (scope.get("session") or {}).get("user_id")
    if user_id:
        return f"user:{user_id}"
    client = scope.get("client")
    return f"addr:{client[0] if client else 'unknown'}"


async def _send_rate_limited(send, retry_after: float) -> None:
    body = ('{"detail":"%s"}' % RATE_LIMITED_DETAIL).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"retry-after", str(max(1, math.ceil(retry_after))).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_too_large(send) -> None:
    body = ('{"detail":"%s"}' % TOO_LARGE_DETAIL).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


CROSS_ORIGIN_DETAIL = "Cross-origin request refused."
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware:
    """Refuses a state-changing browser request whose `Origin` (or, without it, `Referer`) is a site we
    don't serve, answered 403. SameSite=Lax alone lets any same-site origin forge body-less POSTs.

    Allowed: an origin in `trusted_origins`, or one whose host is the request's own `Host` (which
    TrustedHostMiddleware has already restricted). A request with neither header passes: non-browser
    clients send neither, and browsers always send `Origin` on a cross-site POST. `Origin: null`
    (sandboxed frames, some redirects) is refused.
    """

    def __init__(self, app: ASGIApp, *, trusted_origins: Iterable[str]):
        self.app = app
        self.trusted = {origin.rstrip("/").lower() for origin in trusted_origins}

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] == "http" and scope.get("method") in _STATE_CHANGING_METHODS:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            origin = _request_origin(headers)
            if origin is not None and not self._allowed(origin, headers.get(b"host", b"").decode("latin-1")):
                logger.warning("Cross-origin %s %s refused", scope.get("method"), scope.get("path"))
                await _send_cross_origin_refused(send)
                return
        await self.app(scope, receive, send)

    def _allowed(self, origin: str, host: str) -> bool:
        origin = origin.rstrip("/").lower()
        if origin in self.trusted:
            return True
        return origin != "null" and urlsplit(origin).netloc == host.lower()


def _request_origin(headers: dict[bytes, bytes]) -> str | None:
    origin = headers.get(b"origin")
    if origin is not None:
        return origin.decode("latin-1")
    referer = headers.get(b"referer")
    if referer is None:
        return None
    parts = urlsplit(referer.decode("latin-1"))
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else "null"


async def _send_cross_origin_refused(send) -> None:
    body = ('{"detail": "%s"}' % CROSS_ORIGIN_DETAIL).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
