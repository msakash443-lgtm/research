import random
from datetime import datetime, timezone

import httpx
import pytest

from app.connectors.base import ConnectorError
from app.connectors.http import (
    CircuitOpenError,
    ConnectorHttpClient,
    HttpPolicy,
    NotFoundError,
    parse_retry_after,
)


class Env:
    """Scripted server + fake clock; `sleep` advances the clock instead of waiting."""

    def __init__(self, *replies, policy=None, seed=1):
        self.replies = list(replies)
        self.requests = []
        self.now = 1000.0
        self.sleeps = []
        self.client = ConnectorHttpClient(
            "fake",
            "https://api.test",
            policy or HttpPolicy(),
            transport=httpx.MockTransport(self._respond),
            clock=lambda: self.now,
            sleep=self._sleep,
            rng=random.Random(seed),
        )

    def _respond(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def ok(data=None):
    return httpx.Response(200, json=data if data is not None else {"ok": True})


def status(code, **headers):
    return httpx.Response(code, headers=headers, json={})


def test_success_returns_parsed_json_and_sends_params():
    env = Env(ok({"n": 1}))
    assert env.client.get_json("/works", {"q": "x"}) == {"n": 1}
    assert env.requests[0].url.params["q"] == "x"
    assert env.sleeps == []


def test_retries_transient_statuses_then_succeeds():
    env = Env(status(503), status(429), ok())
    assert env.client.get_json("/w") == {"ok": True}
    assert len(env.requests) == 3
    assert len(env.sleeps) == 2


def test_transport_errors_are_retried():
    env = Env(httpx.ConnectError("boom"), ok())
    assert env.client.get_json("/w") == {"ok": True}


def test_gives_up_after_max_retries_loudly():
    env = Env(status(500), policy=HttpPolicy(max_retries=2))
    with pytest.raises(ConnectorError) as info:
        env.client.get_json("/w", {"api_key": "SECRET"})
    assert len(env.requests) == 3
    assert "3 attempts" in str(info.value) and "HTTP 500" in str(info.value)
    assert "SECRET" not in str(info.value) and "api.test" not in str(info.value)


def test_backoff_is_exponentially_capped_with_jitter():
    policy = HttpPolicy(max_retries=5, backoff_base_seconds=1, backoff_max_seconds=4)
    env = Env(status(503), policy=policy)
    with pytest.raises(ConnectorError):
        env.client.get_json("/w")
    caps = [1, 2, 4, 4, 4]
    assert len(env.sleeps) == 5
    assert all(0 <= s <= c for s, c in zip(env.sleeps, caps))
    assert len(set(env.sleeps)) > 1  # jittered, not a fixed schedule


def test_retry_after_is_honoured():
    env = Env(status(429, **{"Retry-After": "7"}), ok(), policy=HttpPolicy(backoff_base_seconds=0.1))
    env.client.get_json("/w")
    assert env.sleeps == [7.0]


def test_excessive_retry_after_stops_instead_of_sleeping():
    env = Env(status(429, **{"Retry-After": "3600"}), ok(), policy=HttpPolicy(max_retry_after_seconds=60))
    with pytest.raises(ConnectorError) as info:
        env.client.get_json("/w")
    assert env.sleeps == [] and len(env.requests) == 1
    assert "not waiting" in str(info.value)


def test_parse_retry_after_forms():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_retry_after("12") == 12
    assert parse_retry_after("Thu, 01 Jan 2026 00:00:30 GMT", now) == 30
    assert parse_retry_after("Wed, 31 Dec 2025 23:00:00 GMT", now) == 0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_404_is_not_retried_and_is_distinguishable():
    env = Env(status(404))
    with pytest.raises(NotFoundError):
        env.client.get_json("/w/1")
    assert len(env.requests) == 1


@pytest.mark.parametrize("code", [400, 401, 403, 422])
def test_other_client_errors_fail_at_once(code):
    env = Env(status(code))
    with pytest.raises(ConnectorError) as info:
        env.client.get_json("/w")
    assert not isinstance(info.value, NotFoundError)
    assert len(env.requests) == 1 and env.sleeps == []


def test_non_json_reply_fails_without_retry():
    env = Env(httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(ConnectorError, match="not valid JSON"):
        env.client.get_json("/w")
    assert len(env.requests) == 1


def test_redirects_are_not_followed():
    env = Env(httpx.Response(302, headers={"Location": "http://169.254.169.254/"}))
    with pytest.raises(ConnectorError):
        env.client.get_json("/w")
    assert len(env.requests) == 1


def test_rate_gate_spaces_requests_per_instance():
    env = Env(ok(), policy=HttpPolicy(min_interval_seconds=2.0))
    for _ in range(3):
        env.client.get_json("/w")
    assert env.sleeps == [2.0, 2.0]  # first call immediate, then spaced

    other = Env(ok(), policy=HttpPolicy(min_interval_seconds=2.0))
    other.client.get_json("/w")
    assert other.sleeps == []  # state is per instance, not process-global


def test_breaker_opens_after_repeated_exhaustion_then_half_opens():
    policy = HttpPolicy(max_retries=0, breaker_threshold=3, breaker_cooldown_seconds=30)
    env = Env(status(503), policy=policy)
    for _ in range(3):
        with pytest.raises(ConnectorError):
            env.client.get_json("/w")
    sent = len(env.requests)
    with pytest.raises(CircuitOpenError):
        env.client.get_json("/w")
    assert len(env.requests) == sent  # no network call while open

    env.now += 31
    env.replies = [ok()]
    assert env.client.get_json("/w") == {"ok": True}  # trial succeeds -> closed
    env.replies = [status(503)]
    with pytest.raises(ConnectorError) as info:
        env.client.get_json("/w")
    assert not isinstance(info.value, CircuitOpenError)  # closed again, failure counted from zero


def test_failed_trial_reopens_the_breaker():
    policy = HttpPolicy(max_retries=0, breaker_threshold=1, breaker_cooldown_seconds=30)
    env = Env(status(503), policy=policy)
    with pytest.raises(ConnectorError):
        env.client.get_json("/w")
    env.now += 31
    with pytest.raises(ConnectorError):
        env.client.get_json("/w")  # the trial, fails
    with pytest.raises(CircuitOpenError):
        env.client.get_json("/w")


def test_bad_requests_and_missing_records_do_not_trip_the_breaker():
    policy = HttpPolicy(breaker_threshold=2)
    for code, exc in ((404, NotFoundError), (400, ConnectorError)):
        env = Env(status(code), policy=policy)
        for _ in range(4):  # consecutive, so a wrongly counted failure would open the breaker
            with pytest.raises(exc) as info:
                env.client.get_json("/w")
            assert not isinstance(info.value, CircuitOpenError)
        env.replies = [ok()]
        assert env.client.get_json("/w") == {"ok": True}


def test_only_one_trial_call_is_allowed_while_half_open():
    policy = HttpPolicy(max_retries=0, breaker_threshold=1, breaker_cooldown_seconds=30)
    env = Env(status(503), policy=policy)
    with pytest.raises(ConnectorError):
        env.client.get_json("/w")
    env.now += 31
    seen = []

    def trial(request):
        try:
            env.client.get_json("/w")  # a second caller arriving while the trial is in flight
        except CircuitOpenError:
            seen.append("blocked")
        return ok()

    env.client._client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(trial))
    assert env.client.get_json("/w") == {"ok": True}
    assert seen == ["blocked"]


def test_invalid_policy_is_rejected():
    for bad in ({"timeout_seconds": 0}, {"max_retries": -1}, {"breaker_threshold": 0}, {"backoff_base_seconds": -1}):
        with pytest.raises(ValueError):
            HttpPolicy(**bad)


def test_post_json_sends_the_body_and_shares_the_retry_policy():
    env = Env(status(503), ok({"n": 2}))
    assert env.client.post_json("/batch", {"ids": ["a"]}, {"fields": "x"}) == {"n": 2}
    assert [r.method for r in env.requests] == ["POST", "POST"]
    assert env.requests[1].content == b'{"ids":["a"]}' and env.requests[1].url.params["fields"] == "x"
    assert len(env.sleeps) == 1  # retried like a GET


def test_post_json_goes_through_the_circuit_breaker():
    env = Env(status(503), policy=HttpPolicy(max_retries=0, breaker_threshold=1))
    with pytest.raises(ConnectorError):
        env.client.post_json("/batch", {})
    with pytest.raises(CircuitOpenError):
        env.client.get_json("/w")  # same breaker for both verbs
