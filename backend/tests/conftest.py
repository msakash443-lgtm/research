import atexit
import contextlib
import os

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

# Test-only database isolation on a real PostgreSQL server. TEST_DATABASE_URL names a database ending
# in `_test` on a server where the role may CREATE DATABASE (for example the `db-test` service in
# docker-compose.yml). Each pytest process (xdist worker) builds its own template database from the
# models and clones it into a working database before every test. The application's own DATABASE_URL
# is never read: the fixtures drop and recreate databases, so a developer's .env must not reach them.
_RAW_TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not _RAW_TEST_URL:
    raise pytest.UsageError(
        "TEST_DATABASE_URL is not set. Point it at a throwaway PostgreSQL database whose name ends in "
        "'_test', e.g. postgresql+psycopg://research_test:<password>@127.0.0.1:55432/research_ai_test "
        "(docker compose --profile test up -d db-test)."
    )
_BASE_URL = make_url(_RAW_TEST_URL)
if not _BASE_URL.drivername.startswith("postgresql") or not (_BASE_URL.database or "").endswith("_test"):
    raise pytest.UsageError("TEST_DATABASE_URL must be a PostgreSQL URL whose database name ends in '_test'")
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
_WORK_URL = _BASE_URL.set(database=f"{_BASE_URL.database}_{_WORKER}")
_TEMPLATE_NAME = f"{_BASE_URL.database}_{_WORKER}_tpl"
_ADMIN_URL = _BASE_URL.set(database="postgres")
_TEMPLATE_URL = _BASE_URL.set(database=_TEMPLATE_NAME)

os.environ["DATABASE_URL"] = _WORK_URL.render_as_string(hide_password=False)
# A test that leaves a transaction open and then writes the same row from another connection would
# wait forever; make that a quick, readable failure instead of a hung run.
os.environ["PGOPTIONS"] = "-c lock_timeout=10000"
os.environ["ENVIRONMENT"] = "development"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["RUN_RESEARCH_INLINE"] = "true"
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-long-enough"
# Every TestClient shares one client address, so the sign-in cap (X.143) would trip across tests;
# test_rate_limit.py turns it back on for itself.
os.environ["RATE_LIMIT_ENABLED"] = "false"
for _ambient_setting in (
    "LLM_API_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LOCAL_LLM_API_BASE_URL",
    "LOCAL_LLM_API_KEY",
    "LOCAL_LLM_MODEL",
    "CONNECTORS_ENABLED",
    "CONNECTORS_ALLOW_SCRAPING",
):
    os.environ.pop(_ambient_setting, None)

from app.config import Settings

Settings.model_config["env_file"] = None

import anyio.from_thread
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

_ADMIN = {}
_TEMPLATE_READY = []


def _conninfo(url) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _admin():
    """One autocommit connection to the maintenance database, for DROP/CREATE DATABASE."""
    if "conn" not in _ADMIN:
        _ADMIN["conn"] = psycopg.connect(_conninfo(_ADMIN_URL), autocommit=True)
        atexit.register(_ADMIN["conn"].close)
    return _ADMIN["conn"]


def _recreate_database(name: str, template: str | None = None) -> None:
    admin = _admin()
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
    statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
    admin.execute(statement)


def _build_template() -> None:
    """Schema (tables, enum types, trigger functions) built once per process from the models.

    Cloning a template per test is far cheaper than create_all, and unlike truncating it also
    restores sequences and anything a test altered with DDL.
    """
    from sqlalchemy import create_engine

    _recreate_database(_TEMPLATE_NAME)
    builder = create_engine(_TEMPLATE_URL)
    try:
        Base.metadata.create_all(bind=builder)
    finally:
        builder.dispose()
    _TEMPLATE_READY.append(True)


@pytest.fixture(autouse=True)
def isolated_database():
    if not _TEMPLATE_READY:
        _build_template()
    engine.dispose()  # a database with open connections cannot be dropped or cloned
    _recreate_database(_WORK_URL.database, template=_TEMPLATE_NAME)
    yield


def _drop_database(name: str) -> None:
    admin = _admin()
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.fixture
def scratch_database_url():
    """URL of an empty throwaway database (no tables), dropped afterwards, for tests that run Alembic."""
    name = f"{_BASE_URL.database}_scratch_{_WORKER}"
    _recreate_database(name)
    yield _BASE_URL.set(database=name).render_as_string(hide_password=False)
    _drop_database(name)


@pytest.fixture
def migrator(scratch_database_url, monkeypatch):
    """(alembic Config, engine) on an empty scratch database; migrations/env.py reads its URL from settings."""
    from pathlib import Path
    from types import SimpleNamespace

    import sqlalchemy as sa
    from alembic.config import Config

    # env.py reads the URL from get_settings(); leave the shared settings cache untouched.
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=scratch_database_url))
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    scratch_engine = sa.create_engine(scratch_database_url)
    yield config, scratch_engine
    scratch_engine.dispose()


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
