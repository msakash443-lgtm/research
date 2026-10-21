import atexit
import contextlib
import os

# Set this before importing app modules. Tests must never reuse a developer's
# DATABASE_URL from .env, because the fixture below drops all test tables.
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ENVIRONMENT"] = "development"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["RUN_RESEARCH_INLINE"] = "true"
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-long-enough"
for _ambient_setting in (
    "LLM_API_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LOCAL_LLM_API_BASE_URL",
    "LOCAL_LLM_API_KEY",
    "LOCAL_LLM_MODEL",
):
    os.environ.pop(_ambient_setting, None)

from app.config import Settings

Settings.model_config["env_file"] = None

import anyio.from_thread
import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from app.database import Base, engine
from tests.llm_replies import chat_reply


_SHARED_PORTAL = {}


@contextlib.contextmanager
def _shared_portal_factory(self):
    """Reuse one event-loop thread for every bare `TestClient(app)` request.

    Starlette starts a fresh blocking portal (a thread plus an event loop) for each request
    unless the client is used as a context manager, which costs ~30 ms per call and dominated
    the suite. Entering the context would also run the app lifespan per test, so instead one
    portal is shared for the whole session.
    """
    if self.portal is not None:
        yield self.portal
        return
    if "portal" not in _SHARED_PORTAL:
        stack = contextlib.ExitStack()
        _SHARED_PORTAL["portal"] = stack.enter_context(anyio.from_thread.start_blocking_portal(**self.async_backend))
        _SHARED_PORTAL["stack"] = stack
        atexit.register(stack.close)
    yield _SHARED_PORTAL["portal"]


TestClient._portal_factory = _shared_portal_factory

_PRISTINE = {}


def _pristine_snapshot():
    """Schema (tables + triggers) built once; restoring it per test is far cheaper than drop/create."""
    import sqlite3

    if "db" not in _PRISTINE:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        live = engine.raw_connection()
        try:
            snapshot = sqlite3.connect(":memory:", check_same_thread=False)
            live.driver_connection.backup(snapshot)
        finally:
            live.close()
        _PRISTINE["db"] = snapshot
    return _PRISTINE["db"]


@pytest.fixture(autouse=True)
def isolated_database():
    snapshot = _pristine_snapshot()
    live = engine.raw_connection()
    try:
        live.rollback()
        snapshot.backup(live.driver_connection)
    finally:
        live.close()
    yield


class FakeLLMEndpoint:
    """Stands in for the HTTP endpoint behind OpenAICompatibleLLM.

    The real adapter code still runs (request building, response parsing, error mapping);
    only the network is replaced. Set `handler` to change the response.
    """

    def __init__(self):
        self.requests = []
        self.sleeps = []
        self.handler = lambda request: chat_reply("Fake answer [S1].")

    def respond(self, request):
        import httpx

        self.requests.append(request)
        result = self.handler(request)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)


@pytest.fixture
def fake_llm(monkeypatch):
    """Configure an LLM and route its HTTP calls to an in-process fake."""
    import types

    import httpx

    from app.agent import llm
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "llm_api_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "llm_model", "fake-model")

    endpoint = FakeLLMEndpoint()
    transport = httpx.MockTransport(endpoint.respond)

    def client_factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    monkeypatch.setattr(llm, "httpx", types.SimpleNamespace(Client=client_factory, HTTPError=httpx.HTTPError))
    # Retry backoff (X.109) is recorded, not slept.
    monkeypatch.setattr(llm, "_sleep", endpoint.sleeps.append, raising=False)
    return endpoint
