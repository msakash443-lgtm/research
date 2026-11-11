import pytest

from app.config import get_settings
from app.connector_breaker_store import DbBreakerSync, build_connector
from app.database import SessionLocal
from app.models import ConnectorBreakerState


@pytest.fixture
def breaker_shared(monkeypatch):
    live = get_settings()
    monkeypatch.setattr(live, "connector_breaker_shared", True)
    return live


def test_load_with_no_row_is_closed():
    assert DbBreakerSync("openalex").load() == (0, None)


def test_save_then_load_round_trips():
    sync = DbBreakerSync("openalex")
    sync.save(3, 12345.5)
    assert sync.load() == (3, 12345.5)
    with SessionLocal() as db:
        assert db.get(ConnectorBreakerState, "openalex").failures == 3


def test_save_twice_updates_the_same_row_not_a_second_one():
    sync = DbBreakerSync("openalex")
    sync.save(1, None)
    sync.save(2, 999.0)
    assert sync.load() == (2, 999.0)
    with SessionLocal() as db:
        assert db.query(ConnectorBreakerState).count() == 1


def test_different_connector_names_do_not_share_state():
    DbBreakerSync("openalex").save(5, 100.0)
    assert DbBreakerSync("crossref").load() == (0, None)


def test_build_connector_does_not_attach_a_sync_by_default():
    connector = build_connector("openalex")
    assert connector._http._breaker_sync is None


def test_build_connector_attaches_a_db_sync_when_enabled(breaker_shared):
    connector = build_connector("openalex")
    sync = connector._http._breaker_sync
    assert isinstance(sync, DbBreakerSync) and sync.name == "openalex"


def test_shared_breaker_trips_across_two_separately_built_connectors(breaker_shared):
    """The whole point of M1.3.8: `build_connector` is called fresh per task, so without a
    shared store the breaker from one failed task is invisible to the next. With the setting
    on, a second, independently built connector for the same name sees the first one's state."""
    first = build_connector("openalex")
    first._http.attach_breaker_sync(DbBreakerSync("openalex"))  # already attached; explicit for clarity
    first._http._breaker_sync.save(10, 99999999999.0)  # far-future open_until

    second = build_connector("openalex")
    from app.connectors.http import CircuitOpenError

    with pytest.raises(CircuitOpenError):
        second._http.get_json("/works")
