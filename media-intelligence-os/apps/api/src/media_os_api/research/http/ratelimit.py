"""Per-source minimum-interval rate limiting for research fetches.

`RedisMinIntervalLimiter` enforces "at most one request per `min_interval_ms`" per source across all
worker processes using `SET key NX PX interval`: the key's existence *is* the cool-down. It fails
**closed** — if Redis is unavailable no request is made and a retryable error is raised (ADR-021
spirit: never exceed an upstream's published limit because our own infrastructure is degraded).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from redis.exceptions import RedisError

from media_os_api.research.http.errors import RetryableFetchError

KEY_PREFIX = "media_os:research:rl:"


class RateLimiter(Protocol):
    def acquire(self, source_code: str, min_interval_ms: int) -> None:
        """Block until a request slot is available, or raise RetryableFetchError."""


class _RedisLike(Protocol):
    def set(self, name: str, value: str, *, nx: bool, px: int) -> Any: ...

    def pttl(self, name: str) -> Any: ...


class RedisMinIntervalLimiter:
    def __init__(
        self,
        client: _RedisLike,
        *,
        max_wait_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._max_wait = max_wait_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def acquire(self, source_code: str, min_interval_ms: int) -> None:
        if min_interval_ms <= 0:
            return
        key = f"{KEY_PREFIX}{source_code}"
        started = self._monotonic()
        while True:
            try:
                if self._client.set(key, "1", nx=True, px=int(min_interval_ms)):
                    return
                remaining_ms = int(self._client.pttl(key))
            except (RedisError, OSError) as exc:
                raise RetryableFetchError(
                    "Research rate limiter unavailable (Redis); refusing to fetch.",
                    code="RATE_LIMITER_UNAVAILABLE",
                ) from exc
            if remaining_ms > 0:
                wait = max(remaining_ms, 10) / 1000.0
            elif remaining_ms == -1:  # key without TTL (should never happen): treat as cool-down
                wait = min_interval_ms / 1000.0
            else:  # -2: expired between SET and PTTL; retry immediately
                wait = 0.01
            if self._monotonic() - started + wait > self._max_wait:
                raise RetryableFetchError(
                    f"Rate-limit slot for '{source_code}' not available within "
                    f"{self._max_wait:.0f}s.",
                    code="RATE_LIMIT_WAIT_EXCEEDED",
                )
            self._sleep(wait)


class InMemoryMinIntervalLimiter:
    """Single-process limiter (tests, offline tooling). Not shared across workers."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sleep = sleep
        self._monotonic = monotonic
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, source_code: str, min_interval_ms: int) -> None:
        with self._lock:
            now = self._monotonic()
            ready_at = self._next_allowed.get(source_code, now)
            wait = max(ready_at - now, 0.0)
            self._next_allowed[source_code] = max(ready_at, now) + min_interval_ms / 1000.0
        if wait > 0:
            self._sleep(wait)


def build_redis_rate_limiter(
    redis_url: str, *, socket_timeout: float = 2.0
) -> RedisMinIntervalLimiter:
    """Construct the production limiter from a Redis DSN (never logged)."""
    from redis import Redis

    client = Redis.from_url(
        redis_url, socket_timeout=socket_timeout, socket_connect_timeout=socket_timeout
    )
    return RedisMinIntervalLimiter(client)
