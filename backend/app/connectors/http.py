"""Shared HTTP client for connectors: timeouts, retries, rate gate, circuit breaker.

One `ConnectorHttpClient` per connector instance. All state (last-request time, failure count,
breaker) lives on the instance, never at module level, so two connectors (or two tests) cannot
throttle or trip each other.

Failure policy (plan rule 22: fail loudly, never return placeholder data):
* 429 and 5xx (502/503/504 and 500) and transport errors are retried with exponential backoff
  and full jitter; a `Retry-After` header is honoured when it asks for longer than the backoff.
  If the service asks for longer than `max_retry_after`, we stop rather than sleep for minutes.
* 404 raises `NotFoundError` (callers may map it to "no such record"); other 4xx raise
  `ConnectorError` immediately and are not retried.
* A reply that is not JSON raises `ConnectorError`, not retried.
* After `breaker_threshold` consecutive calls that exhausted their retries, the circuit opens and
  calls fail at once with `CircuitOpenError` until `breaker_cooldown` passes; then one trial call
  is allowed (success closes the circuit, failure reopens it).
* Optional response cache (off unless `cache_ttl_seconds` > 0, i.e. `CONNECTOR_CACHE_TTL_SECONDS`): a
  repeat of the same connector + method + path + params + body within the TTL is answered from memory
  *before* the rate gate, breaker and network. Only successful JSON replies are stored (never errors or
  404s). `cache_hits`/`cache_misses` and `last_from_cache` let callers record that an answer was cached;
  a search that must be fresh passes `fresh=True` or runs inside `bypass_cache()`. Headers are not part of the key.
* Redirects are not followed. Error messages carry the connector name and status only, never the
  URL, query or headers (they can hold API keys or the researcher's search terms).
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import json
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from app.config import get_settings
from app.connectors.base import ConnectorError

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Set while a block of code must see live answers (a database search). Per thread/task, never global.
_BYPASS_CACHE: contextvars.ContextVar[bool] = contextvars.ContextVar("connector_bypass_cache", default=False)


@contextlib.contextmanager
def bypass_cache():
    """Inside this block every connector request skips the response cache (and refreshes it)."""
    token = _BYPASS_CACHE.set(True)
    try:
        yield
    finally:
        _BYPASS_CACHE.reset(token)


class NotFoundError(ConnectorError):
    """The service answered 404."""


class CircuitOpenError(ConnectorError):
    """Recent calls kept failing; this connector is paused until the cooldown ends."""


@dataclass(frozen=True)
class HttpPolicy:
    timeout_seconds: float = 20.0
    max_retries: int = 3  # retries after the first attempt
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 30.0
    max_retry_after_seconds: float = 60.0
    min_interval_seconds: float = 0.0  # rate gate: minimum spacing between request starts
    breaker_threshold: int = 5
    breaker_cooldown_seconds: float = 60.0
    cache_ttl_seconds: float = field(default_factory=lambda: get_settings().connector_cache_ttl_seconds)  # 0 = no cache
    cache_max_entries: int = 256

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.max_retries < 0 or self.breaker_threshold < 1:
            raise ValueError("invalid HttpPolicy")
        if min(self.backoff_base_seconds, self.backoff_max_seconds, self.max_retry_after_seconds,
               self.min_interval_seconds, self.breaker_cooldown_seconds, self.cache_ttl_seconds) < 0 or self.cache_max_entries < 1:
            raise ValueError("invalid HttpPolicy")


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date); None if absent/invalid."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


class ConnectorHttpClient:
    def __init__(
        self,
        name: str,
        base_url: str,
        policy: HttpPolicy | None = None,
        *,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ):
        self.name = name
        self.policy = policy or HttpPolicy()
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._next_slot = 0.0  # earliest time the next request may start
        self._failures = 0
        self._open_until: float | None = None
        self._trial_in_flight = False
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0
        self.last_from_cache = False
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=self.policy.timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # --- breaker -------------------------------------------------------------

    def _before_call(self) -> None:
        with self._lock:
            if self._open_until is None:
                return
            if self._clock() < self._open_until or self._trial_in_flight:
                raise CircuitOpenError(f"{self.name} is temporarily unavailable after repeated failures.")
            self._trial_in_flight = True  # half-open: exactly one trial call

    def _record(self, ok: bool) -> None:
        with self._lock:
            self._trial_in_flight = False
            if ok:
                self._failures = 0
                self._open_until = None
                return
            self._failures += 1
            if self._failures >= self.policy.breaker_threshold:
                self._open_until = self._clock() + self.policy.breaker_cooldown_seconds

    # --- rate gate -----------------------------------------------------------

    def _wait_for_slot(self) -> None:
        interval = self.policy.min_interval_seconds
        if interval <= 0:
            return
        with self._lock:
            now = self._clock()
            start = max(now, self._next_slot)
            self._next_slot = start + interval
        if start > now:
            self._sleep(start - now)

    # --- requests ------------------------------------------------------------

    def _backoff(self, attempt: int) -> float:
        cap = min(self.policy.backoff_max_seconds, self.policy.backoff_base_seconds * (2**attempt))
        return self._rng.uniform(0, cap)

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, *, fresh: bool = False
    ) -> Any:
        return self._request_json("GET", path, params, headers, None, fresh)

    def get_text(
        self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, *, fresh: bool = False
    ) -> str:
        """GET a text reply (e.g. an Atom feed); same retry/gate/breaker/cache policy as `get_json`."""
        return self._request_json("GET", path, params, headers, None, fresh, as_text=True)

    def post_json(
        self, path: str, body: Any, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
        *, fresh: bool = False,
    ) -> Any:
        """POST a JSON body for read-only batch lookups; same retry/gate/breaker policy as `get_json`."""
        return self._request_json("POST", path, params, headers, body, fresh)

    def post(
        self, path: str, body: Any, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
        *, fresh: bool = False,
    ) -> httpx.Response:
        """POST and return the raw 2xx response: for services whose success reply is not a JSON body
        (e.g. an empty reply carrying the new resource id in a header). Never cached; the same
        retry/rate-gate/breaker policy as `post_json` applies."""
        return self._request_json("POST", path, params, headers, body, fresh, as_response=True)

    # --- cache ---------------------------------------------------------------

    def _cache_key(self, method: str, path: str, params, body, as_text: bool = False) -> str:
        raw = json.dumps([self.name, method, path, params, body, as_text], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False, None
            stored_at, value = entry
            if self._clock() - stored_at >= self.policy.cache_ttl_seconds:
                del self._cache[key]
                return False, None
            self._cache.move_to_end(key)
            return True, copy.deepcopy(value)

    def _cache_put(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = (self._clock(), copy.deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.policy.cache_max_entries:
                self._cache.popitem(last=False)

    def _request_json(self, method: str, path: str, params, headers, body, fresh: bool = False, *, as_text: bool = False,
                      as_response: bool = False) -> Any:
        caching = self.policy.cache_ttl_seconds > 0 and not as_response  # raw responses are never cached
        fresh = fresh or _BYPASS_CACHE.get()
        key = self._cache_key(method, path, params, body, as_text) if caching else ""
        if caching and not fresh:
            hit, value = self._cache_get(key)
            if hit:
                self.cache_hits += 1
                self.last_from_cache = True
                return value
        self.last_from_cache = False
        if caching:
            self.cache_misses += 1
        result = self._request_json_network(method, path, params, headers, body, as_text, as_response)
        if caching:
            self._cache_put(key, result)
        return result

    def _request_json_network(self, method: str, path: str, params, headers, body, as_text: bool = False,
                              as_response: bool = False) -> Any:
        self._before_call()
        try:
            result = self._get_json_with_retries(method, path, params, headers, body, as_text, as_response)
        except NotFoundError:
            self._record(True)  # the service is healthy; the record just isn't there
            raise
        except _Exhausted as exc:
            self._record(False)
            raise ConnectorError(str(exc)) from None
        except ConnectorError:
            self._record(True)  # our request was bad or the reply unusable; not a service outage
            raise
        self._record(True)
        return result

    def _get_json_with_retries(self, method, path, params, headers, body, as_text: bool = False,
                               as_response: bool = False) -> Any:
        last = "no response"
        for attempt in range(self.policy.max_retries + 1):
            self._wait_for_slot()
            retry_after: float | None = None
            try:
                response = self._client.request(method, path, params=params, headers=headers, json=body)
            except httpx.HTTPError as exc:
                last = f"network error ({type(exc).__name__})"
            else:
                status = response.status_code
                if status == 404:
                    raise NotFoundError(f"{self.name}: not found")
                if status in RETRY_STATUSES:
                    last = f"HTTP {status}"
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                elif status >= 400:
                    raise ConnectorError(f"{self.name}: request rejected (HTTP {status})")
                elif as_response:
                    return response
                elif as_text:
                    return response.text
                else:
                    try:
                        return response.json()
                    except ValueError:
                        raise ConnectorError(f"{self.name}: reply was not valid JSON") from None
            if attempt == self.policy.max_retries:
                break
            delay = self._backoff(attempt)
            if retry_after is not None:
                if retry_after > self.policy.max_retry_after_seconds:
                    raise _Exhausted(f"{self.name}: asked to wait {int(retry_after)}s ({last}); not waiting that long")
                delay = max(delay, retry_after)
            self._sleep(delay)
        raise _Exhausted(f"{self.name}: failed after {self.policy.max_retries + 1} attempts ({last})")


class _Exhausted(Exception):
    """Internal: retries used up or the service asked for too long a wait."""
