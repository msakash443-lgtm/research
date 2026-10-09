from __future__ import annotations

from collections.abc import Awaitable, Callable

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


class ContentLengthLimitMiddleware:
    """Reject obviously oversized request bodies before FastAPI reads them into memory.

    A reverse proxy should enforce the same limit. Requests without a Content-Length header fall
    through to this check (so declared-length requests are rejected fast) and then to the route's
    own streaming cap for file uploads (M2.7.2's manual-upload endpoint), which protects against a
    missing or false Content-Length regardless of this middleware.

    `upload_path_suffix`/`upload_max_bytes` let one route (the manual full-text upload) use a larger
    cap than the default, which stays small for every ordinary JSON/text request.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int,
        *,
        upload_path_suffix: str | None = None,
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
