"""The connector response cache is read before the network and is off by default (plan M1.3.3)."""
import httpx
import pytest

from app.connectors.base import ConnectorError
from app.connectors.http import HttpPolicy, NotFoundError
from tests.test_connector_http import Env, ok, status


def cached(*replies, ttl=60.0, **policy):
    return Env(*replies, policy=HttpPolicy(cache_ttl_seconds=ttl, **policy))


def test_it_is_off_by_default_so_every_call_reaches_the_network():
    env = Env(ok({"n": 1}))
    env.client.get_json("/w", {"q": "x"})
    env.client.get_json("/w", {"q": "x"})
    assert len(env.requests) == 2 and env.client.cache_hits == 0 and env.client.last_from_cache is False


def test_a_repeat_within_the_ttl_is_answered_without_any_network_call():
    env = cached(ok({"n": 1}))
    assert env.client.get_json("/w", {"q": "x"}) == {"n": 1}
    assert env.client.last_from_cache is False
    assert env.client.get_json("/w", {"q": "x"}) == {"n": 1}
    assert len(env.requests) == 1 and env.client.last_from_cache is True
    assert (env.client.cache_hits, env.client.cache_misses) == (1, 1)


def test_different_params_path_or_method_are_different_entries():
    env = cached(ok({"n": 1}))
    env.client.get_json("/w", {"q": "x"})
    env.client.get_json("/w", {"q": "y"})
    env.client.get_json("/other", {"q": "x"})
    env.client.post_json("/w", {"ids": [1]}, {"q": "x"})
    env.client.post_json("/w", {"ids": [2]}, {"q": "x"})
    assert len(env.requests) == 5


def test_param_order_does_not_matter():
    env = cached(ok())
    env.client.get_json("/w", {"a": 1, "b": 2})
    env.client.get_json("/w", {"b": 2, "a": 1})
    assert len(env.requests) == 1


def test_an_entry_expires_after_the_ttl():
    env = cached(ok({"n": 1}), ttl=30)
    env.client.get_json("/w")
    env.now += 31
    env.client.get_json("/w")
    assert len(env.requests) == 2


def test_fresh_bypasses_the_cache_and_refreshes_it():
    env = cached(ok({"n": 1}), ok({"n": 2}))
    env.client.get_json("/w")
    assert env.client.get_json("/w", fresh=True) == {"n": 2}
    assert env.client.get_json("/w") == {"n": 2}
    assert len(env.requests) == 2


def test_errors_are_never_cached():
    env = cached(status(404), ok({"n": 1}))
    with pytest.raises(NotFoundError):
        env.client.get_json("/w")
    assert env.client.get_json("/w") == {"n": 1}
    bad = cached(status(400), ok())
    with pytest.raises(ConnectorError):
        bad.client.get_json("/w")
    bad.client.get_json("/w")
    assert len(bad.requests) == 2


def test_a_cache_hit_does_not_use_the_rate_gate_or_the_breaker():
    env = cached(ok(), min_interval_seconds=5, breaker_threshold=1, max_retries=0)
    env.client.get_json("/w")
    slept = list(env.sleeps)
    for _ in range(3):
        env.client.get_json("/w")
    assert env.sleeps == slept and len(env.requests) == 1


def test_callers_cannot_change_what_is_cached():
    env = cached(ok({"items": [1]}))
    first = env.client.get_json("/w")
    first["items"].append(99)
    assert env.client.get_json("/w") == {"items": [1]}


def test_the_cache_is_bounded():
    env = cached(ok(), cache_max_entries=2)
    for i in range(3):
        env.client.get_json("/w", {"i": i})
    env.client.get_json("/w", {"i": 0})  # evicted, so the network again
    assert len(env.requests) == 4


def test_each_client_has_its_own_cache():
    a, b = cached(ok()), cached(ok())
    a.client.get_json("/w")
    b.client.get_json("/w")
    assert len(a.requests) == 1 and len(b.requests) == 1


def test_a_negative_ttl_is_refused():
    with pytest.raises(ValueError):
        HttpPolicy(cache_ttl_seconds=-1)
