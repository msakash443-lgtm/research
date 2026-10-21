from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.exceptions import HTTPException

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


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
