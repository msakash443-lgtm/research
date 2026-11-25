import atexit
import contextlib
import os
import shutil
import uuid
import zlib

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import make_url

# Test-only database isolation on a real PostgreSQL server. TEST_DATABASE_URL names a database ending
# in `_test` on a server where the role may CREATE DATABASE (for example the `db-test` service in
# docker-compose.yml). Each pytest process (xdist worker) builds its own template database from the
# models and keeps one working database cloned from it; see `isolated_database` for how each test is
# isolated. The application's own DATABASE_URL is never read: the fixtures drop and recreate databases,
# so a developer's .env must not reach them. The one value taken from .env is TEST_DATABASE_URL itself
# (a process environment variable wins); `dotenv_values` returns it without touching os.environ.
_RAW_TEST_URL = os.environ.get("TEST_DATABASE_URL") or dotenv_values(".env").get("TEST_DATABASE_URL")
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
# One id per test run (xdist hands the controller's id to every worker), so a template left by an earlier
# run, built from older models, is never reused.
_RUN_UID = (os.environ.get("PYTEST_XDIST_TESTRUNUID") or uuid.uuid4().hex)[:12]  # keeps database names under 63 bytes
_TEMPLATE_NAME = f"{_BASE_URL.database}_tpl_{_RUN_UID}"
_ADMIN_URL = _BASE_URL.set(database="postgres")

os.environ["DATABASE_URL"] = _WORK_URL.render_as_string(hide_password=False)
# A test that leaves a transaction open and then writes the same row from another connection would
# wait forever; make that a quick, readable failure instead of a hung run.
os.environ["PGOPTIONS"] = "-c lock_timeout=10000 -c search_path=public,pg_catalog"
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
    "LLM_QUOTA_FALLBACK_TO_LOCAL",
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
        # FILE_COPY (PostgreSQL 15+) copies the template's files; the default WAL_LOG copies page by page
        # through the WAL and was about twice as slow to clone a 12 MB template on Windows.
        statement += sql.SQL(" TEMPLATE {} STRATEGY = FILE_COPY").format(sql.Identifier(template))
    admin.execute(statement)


def _build_template() -> None:
    """Schema (tables, enum types, trigger functions) built from the models once per test run.

    Every xdist worker clones its working database from the one template, instead of each worker
    creating ~200 tables itself (about 10 s each, and slower when eight do it at once). The first worker to
    arrive builds it under an advisory lock; the others wait for the lock, find it, and carry on. It is
    built under a temporary name and renamed when complete, so a crashed builder never leaves a half-built
    template behind. The pytest process that started the run drops it at the end (`pytest_sessionfinish`).
    """
    from sqlalchemy import create_engine

    admin = _admin()
    lock_key = zlib.crc32(_TEMPLATE_NAME.encode())
    admin.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
    try:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEMPLATE_NAME,)).fetchone() is None:
            building = f"{_TEMPLATE_NAME}_building"
            _recreate_database(building)
            builder = create_engine(_BASE_URL.set(database=building))
            try:
                # now() is fixed at transaction start, so in rollback mode every row a test writes would
                # share one timestamp (breaking newest-first ordering and paging). A `public.now()` that
                # reads the clock, found ahead of pg_catalog through the search_path set in PGOPTIONS,
                # gives each row its own time, as separate requests do in production. Column defaults
                # bind to it at create_all.
                with builder.begin() as setup:
                    setup.exec_driver_sql(
                        "CREATE FUNCTION public.now() RETURNS timestamptz LANGUAGE sql VOLATILE "
                        "AS 'SELECT clock_timestamp()'"
                    )
                Base.metadata.create_all(bind=builder)
            finally:
                builder.dispose()
            admin.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(building), sql.Identifier(_TEMPLATE_NAME)))
    finally:
        admin.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    _TEMPLATE_READY.append(True)


def pytest_configure(config):
    """Make the run id (and so the template's name) the same in the controller and every xdist worker."""
    if not hasattr(config, "workerinput") and getattr(config.option, "testrunuid", None) is None:
        config.option.testrunuid = _RUN_UID


def pytest_sessionfinish(session):
    """The process that started the run (not an xdist worker) drops the run's template."""
    if not hasattr(session.config, "workerinput"):
        _drop_database(_TEMPLATE_NAME)


def pytest_collection_modifyitems(config, items):
    """Tests marked `requires_graphviz` skip with a stated reason when Settings.graphviz_dot_path cannot be found."""
    from app.config import get_settings

    dot_path = get_settings().graphviz_dot_path
    if shutil.which(dot_path) is not None:
        return
    skip = pytest.mark.skip(reason=f"Graphviz `dot` not found at {dot_path!r}; install Graphviz or set GRAPHVIZ_DOT_PATH")
    for item in items:
        if item.get_closest_marker("requires_graphviz") is not None:
            item.add_marker(skip)


# Isolation modes. "rollback" (default): one working database per worker, each test runs inside an
# outer transaction that is rolled back, with SessionLocal joined to it through savepoints, so a
# test's commits never reach the database. "clone": the working database is dropped and re-cloned
# from the template before the test. Tests that need real commits, a second connection, lock
# contention, DDL or Alembic are marked `@pytest.mark.real_db` and always get "clone".
# RESEARCH_TEST_ISOLATION=clone forces clone mode for every test (to compare the two modes).
_ISOLATION = os.environ.setdefault("RESEARCH_TEST_ISOLATION", "rollback")  # read by modules that scope fixtures on it
_WORK_DB_DIRTY = [True]  # the working database may hold committed data (or does not exist yet)


def _reset_work_database() -> None:
    engine.dispose()  # a database with open connections cannot be dropped or cloned
    _recreate_database(_WORK_URL.database, template=_TEMPLATE_NAME)
    _WORK_DB_DIRTY[0] = False


@pytest.fixture(scope="module")
def shared_world_database():
    """A clean working database in which a module-scoped fixture may commit a world that every test of
    the module then shares; each test's own changes still roll back on top of it (rollback mode only).
    The database is marked dirty afterwards so the next module starts from the template again."""
    if not _TEMPLATE_READY:
        _build_template()
    _reset_work_database()
    yield
    _WORK_DB_DIRTY[0] = True


def _make_rollback_mode_session_class():
    """A Session whose close() releases, instead of rolling back, a savepoint it never wrote through.

    All sessions of a rollback-mode test share one connection, so their savepoints nest. The request's
    own session can still be open (holding a savepoint it only read through) while an inline research run
    opens and commits other sessions inside it; rolling that outer savepoint back on close would silently
    undo the inner sessions' committed work. Production gives every session its own connection, so this
    only matters here. Writing sessions still roll back normally.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    class RollbackModeSession(Session):
        def close(self):
            transaction = self.get_transaction()
            if transaction is not None and transaction.is_active and not self.info.get("wrote"):
                self.commit()
            super().close()

        def commit(self):
            try:
                super().commit()
            finally:
                self.info.pop("wrote", None)

        def rollback(self):
            try:
                super().rollback()
            finally:
                self.info.pop("wrote", None)

    @event.listens_for(RollbackModeSession, "do_orm_execute")
    def _mark_writes(state):
        if not state.is_select:
            state.session.info["wrote"] = True

    @event.listens_for(RollbackModeSession, "before_flush")  # before, so a flush that fails still counts
    def _mark_flush(session, flush_context, instances):
        session.info["wrote"] = True

    return RollbackModeSession


_ROLLBACK_SESSION_CLASS = _make_rollback_mode_session_class()


@pytest.fixture(autouse=True)
def isolated_database(request):
    from sqlalchemy import event

    from app.database import SessionLocal

    if not _TEMPLATE_READY:
        _build_template()
    if (
        _ISOLATION == "clone"
        or request.node.get_closest_marker("real_db") is not None
        or "race_db" in request.fixturenames
    ):
        _reset_work_database()
        _WORK_DB_DIRTY[0] = True
        yield
        return
    if _WORK_DB_DIRTY[0]:
        _reset_work_database()
    connection = engine.connect()
    outer = connection.begin()
    strays = []

    def _stray_checkout(dbapi_connection, record, proxy):
        strays.append(1)
        raise AssertionError(
            "this test opened a database connection of its own, which bypasses the per-test rollback "
            "and would leak committed rows into later tests; mark it @pytest.mark.real_db"
        )

    event.listen(engine, "checkout", _stray_checkout)
    original_session_class = SessionLocal.class_
    SessionLocal.class_ = _ROLLBACK_SESSION_CLASS
    SessionLocal.configure(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield
    finally:
        event.remove(engine, "checkout", _stray_checkout)
        SessionLocal.class_ = original_session_class
        SessionLocal.configure(bind=engine)
        SessionLocal.kw.pop("join_transaction_mode", None)
        outer.rollback()
        connection.close()
    assert not strays, "a test bypassed the per-test rollback transaction; mark it @pytest.mark.real_db"


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
    from alembic import command as alembic_command
    from alembic.config import Config

    # env.py reads the URL from get_settings(); leave the shared settings cache untouched.
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=scratch_database_url))
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    scratch_engine = sa.create_engine(scratch_database_url)
    scratch_name = make_url(scratch_database_url).database
    monkeypatch.setattr(alembic_command, "upgrade", _cached_upgrade(alembic_command.upgrade, scratch_engine, scratch_name))
    yield config, scratch_engine
    scratch_engine.dispose()


# Databases already migrated to a revision, built once per process: {revision id: database name}.
_MIGRATED = {}


def _cached_upgrade(real_upgrade, scratch_engine, scratch_name):
    """`alembic.command.upgrade`, except that upgrading an empty scratch database starts from a clone of
    the nearest earlier revision this process already migrated to, and a fresh upgrade is kept for reuse.

    Running ~100 migrations from nothing took 10-25 s per test; the revisions only have to be walked once
    per worker. Only an upgrade that starts from an empty database is cached, so a stored copy never holds
    a test's own rows, and an upgrade that does not start empty runs for real.
    """

    def upgrade(config, revision, sql=False, tag=None):
        from alembic.script import ScriptDirectory
        from sqlalchemy import inspect

        script = ScriptDirectory.from_config(config)
        target = script.get_current_head() if revision == "head" else revision
        scratch_engine.dispose()
        starts_empty = "alembic_version" not in inspect(scratch_engine).get_table_names()
        if sql or not starts_empty or script.get_revision(target) is None:
            return real_upgrade(config, revision, sql=sql, tag=tag)
        for ancestor in script.iterate_revisions(target, "base"):  # the target first, then back toward base
            if ancestor.revision in _MIGRATED:
                scratch_engine.dispose()
                _recreate_database(scratch_name, template=_MIGRATED[ancestor.revision])
                break
        real_upgrade(config, target)
        if target not in _MIGRATED:
            scratch_engine.dispose()
            stored = f"{_BASE_URL.database}_mig_{_WORKER}_{target}"
            _recreate_database(stored, template=scratch_name)
            _MIGRATED[target] = stored

    return upgrade


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
