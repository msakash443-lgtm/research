"""X.143: sign-in and upload requests are capped per caller per minute (429 + Retry-After)."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, get_settings
from app.middleware import FixedWindowCounter


@pytest.fixture()
def limits(monkeypatch):
    """Rate limiting on (conftest turns it off for the rest of the suite), small caps, a fake clock."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    monkeypatch.setattr(get_settings(), "rate_limit_upload_per_minute", 2)
    monkeypatch.setattr(main.rate_limit_counter, "now", lambda: clock["now"])
    main.rate_limit_counter.clear()
    yield clock
    main.rate_limit_counter.clear()


def _dev_login(client, email="limit@example.com"):
    return client.post("/api/auth/development/login", json={"email": email, "display_name": "L"})


def test_rate_limiting_is_on_by_default():
    fields = Settings.model_fields
    assert fields["rate_limit_enabled"].default is True
    assert fields["rate_limit_auth_per_minute"].default == 20
    assert fields["rate_limit_upload_per_minute"].default == 30


def test_the_request_after_the_cap_gets_429_with_retry_after(limits):
    # Fresh clients stay anonymous, so all four count against the one client address.
    assert [_dev_login(TestClient(main.app)).status_code for _ in range(3)] == [200, 200, 200]

    refused = _dev_login(TestClient(main.app), "x@example.com")

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert refused.json() == {"detail": "Too many requests. Try again shortly."}


def test_the_window_resets_after_a_minute(limits):
    client = TestClient(main.app)
    for _ in range(3):
        _dev_login(TestClient(main.app))
    limits["now"] += 45
    blocked = _dev_login(client)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "15"

    limits["now"] += 15

    assert _dev_login(client).status_code == 200


def test_signed_in_users_are_counted_separately_from_each_other(limits):
    alice, bob = TestClient(main.app), TestClient(main.app)
    assert _dev_login(alice, "alice@example.com").status_code == 200  # anonymous bucket: 1 of 3
    assert _dev_login(bob, "bob@example.com").status_code == 200      # anonymous bucket: 2 of 3
    # Now signed in, each is counted under their own user id, not the shared address.
    assert [_dev_login(alice, "alice@example.com").status_code for _ in range(3)] == [200, 200, 200]
    assert _dev_login(alice, "alice@example.com").status_code == 429
    assert [_dev_login(bob, "bob@example.com").status_code for _ in range(3)] == [200, 200, 200]


def test_upload_paths_have_their_own_cap(limits):
    client = TestClient(main.app)
    assert _dev_login(client).status_code == 200
    statuses = [client.post("/api/notes/voice", content=b"x").status_code for _ in range(3)]
    assert statuses[:2] != [429, 429]
    assert statuses[2] == 429
    # A GET to the same path, and other routes, are not counted.
    assert client.get("/api/auth/me").status_code == 200


def test_other_routes_are_never_limited(limits):
    client = TestClient(main.app)
    _dev_login(client)
    assert all(client.get("/api/auth/me").status_code == 200 for _ in range(10))
    assert all(client.get("/api/auth/methods").status_code == 200 for _ in range(10))


def test_nothing_is_limited_when_switched_off(limits, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    client = TestClient(main.app)
    assert all(_dev_login(client).status_code == 200 for _ in range(10))


def test_the_counter_forgets_ended_windows_once_it_is_large():
    now = {"t": 0.0}
    counter = FixedWindowCounter(now=lambda: now["t"])
    for i in range(10_001):
        counter.hit("auth", f"addr:{i}", 1)
    now["t"] = 61
    counter.hit("auth", "addr:new", 1)
    assert len(counter._windows) == 1
