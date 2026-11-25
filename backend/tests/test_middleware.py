"""X.112: `ContentLengthLimitMiddleware` must cap the bytes actually received, not only a declared
`Content-Length`. A chunked body, or one with an unparseable length, used to pass straight through to
the route, so an ordinary JSON route would buffer it whole."""

import asyncio

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware import ContentLengthLimitMiddleware
from tests.api_helpers import sign_in

LIMIT = 100


def _app():
    inner = FastAPI()
    seen = []

    @inner.post("/json")
    def json_route(payload: dict):
        seen.append(payload)
        return {"ok": True}

    @inner.post("/raw")
    async def raw_route(request: Request):
        body = await request.body()
        seen.append(body)
        return {"size": len(body)}

    inner.add_middleware(ContentLengthLimitMiddleware, max_body_bytes=LIMIT)
    return inner, seen


def _chunks(*parts):
    def gen():
        yield from parts
    return gen()


def test_a_chunked_json_body_over_the_limit_is_refused_with_413_and_the_route_never_runs():
    inner, seen = _app()
    client = TestClient(inner)
    body = b'{"text": "' + b"x" * 300 + b'"}'

    response = client.post(
        "/json", content=_chunks(body[:50], body[50:]), headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large."}
    assert seen == []


def test_a_chunked_raw_body_over_the_limit_is_refused_with_413():
    inner, seen = _app()
    client = TestClient(inner)

    response = client.post("/raw", content=_chunks(b"a" * 60, b"b" * 60))

    assert response.status_code == 413
    assert seen == []


def test_a_chunked_body_within_the_limit_still_reaches_the_route():
    inner, seen = _app()
    client = TestClient(inner)

    response = client.post("/raw", content=_chunks(b"a" * 40, b"b" * 60))

    assert response.status_code == 200
    assert response.json() == {"size": LIMIT}
    assert seen == [b"a" * 40 + b"b" * 60]


def test_an_unparseable_content_length_does_not_switch_the_limit_off():
    inner, seen = _app()
    messages = [
        {"type": "http.request", "body": b"a" * 80, "more_body": True},
        {"type": "http.request", "body": b"b" * 80, "more_body": False},
    ]
    sent = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/raw", "raw_path": b"/raw", "root_path": "", "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-length", b"not-a-number")],
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }

    asyncio.run(inner(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert seen == []


def test_a_declared_length_over_the_limit_is_still_refused_before_the_body_is_read():
    inner, seen = _app()
    client = TestClient(inner)

    response = client.post("/raw", content=b"a" * (LIMIT + 1))

    assert response.status_code == 413
    assert seen == []


def _signed_in_client():
    return sign_in("origin-check@example.com")


def test_a_cross_origin_post_is_refused_and_the_session_survives():
    client = _signed_in_client()

    refused = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})

    assert refused.status_code == 403
    assert refused.json() == {"detail": "Cross-origin request refused."}
    assert client.get("/api/auth/me").status_code == 200


def test_a_same_origin_post_without_or_with_a_matching_origin_is_allowed():
    assert _signed_in_client().post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code == 204
    assert _signed_in_client().post("/api/auth/logout").status_code == 204


def test_a_referer_from_another_site_is_refused_when_there_is_no_origin_header():
    client = _signed_in_client()

    assert client.post("/api/auth/logout", headers={"Referer": "https://evil.example/x"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"Referer": "http://testserver/page"}).status_code == 204


def test_an_opaque_null_origin_is_refused():
    assert _signed_in_client().post("/api/auth/logout", headers={"Origin": "null"}).status_code == 403


def test_a_get_with_a_foreign_origin_is_not_refused():
    assert _signed_in_client().get("/api/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200


def test_a_configured_public_origin_is_trusted_even_when_the_host_differs():
    from fastapi.testclient import TestClient

    inner = FastAPI()

    @inner.post("/x")
    def post_x():
        return {"ok": True}

    from app.middleware import OriginCheckMiddleware

    inner.add_middleware(OriginCheckMiddleware, trusted_origins=["https://app.example.org/"])
    client = TestClient(inner)

    assert client.post("/x", headers={"Origin": "https://APP.example.org"}).status_code == 200
    assert client.post("/x", headers={"Origin": "https://other.example.org"}).status_code == 403
