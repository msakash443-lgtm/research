from __future__ import annotations

from collections.abc import Awaitable, Callable

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


class ContentLengthLimitMiddleware:
    """Reject obviously oversized request bodies before FastAPI reads them into memory.

    A reverse proxy should enforce the same limit. Requests without a
    Content-Length header are still handled by FastAPI's field limits; future
    file uploads need a streaming body limiter before being enabled.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            raw_length = headers.get(b"content-length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    content_length = 0
                if content_length > self.max_body_bytes:
                    body = b'{"detail":"Request body is too large."}'
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
                    return
        await self.app(scope, receive, send)
