"""Free-tier quota tracking per (provider, model) route.

Two mechanisms work together:

1. **Self-imposed caps** (requests per minute / per day) that stay at or below each free
   tier, so the router switches *before* a provider starts refusing.
2. **Observed blocks**: when a provider answers 429 / quota-exhausted, the route is blocked
   until `Retry-After` or the next daily reset, and the router fails over immediately.

Counters live in Redis in production (shared by API and worker) and in memory for tests.
If Redis is unavailable the tracker fails closed (no request is made), like the research
limiter.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError

from media_os_api.ai.errors import AIUnavailable
from media_os_api.ai.types import ModelRoute

KEY_PREFIX = "media_os:ai:"


@dataclass(frozen=True)
class QuotaLimits:
    rpm: int | None = None
    rpd: int | None = None
    reset_timezone: str = "UTC"  # where the provider's daily quota resets at local midnight


class QuotaStore(Protocol):
    def incr(self, key: str, ttl_seconds: int) -> int: ...

    def get_int(self, key: str) -> int: ...

    def set_until(self, key: str, until_epoch: float) -> None: ...

    def get_until(self, key: str) -> float | None: ...


class InMemoryQuotaStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counts: dict[str, tuple[int, float]] = {}
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def incr(self, key: str, ttl_seconds: int) -> int:
        with self._lock:
            now = self._clock()
            count, expires = self._counts.get(key, (0, now + ttl_seconds))
            if expires <= now:
                count, expires = 0, now + ttl_seconds
            self._counts[key] = (count + 1, expires)
            return count + 1

    def get_int(self, key: str) -> int:
        with self._lock:
            count, expires = self._counts.get(key, (0, 0.0))
            return count if expires > self._clock() else 0

    def set_until(self, key: str, until_epoch: float) -> None:
        with self._lock:
            self._until[key] = max(until_epoch, self._until.get(key, 0.0))

    def get_until(self, key: str) -> float | None:
        with self._lock:
            until = self._until.get(key)
            return until if until and until > self._clock() else None


class RedisQuotaStore:
    def __init__(self, client: Any) -> None:
        self._r = client

    def _guard(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except (RedisError, OSError) as exc:
            raise AIUnavailable(
                "AI quota store (Redis) unavailable; refusing to call providers."
            ) from exc

    def incr(self, key: str, ttl_seconds: int) -> int:
        def run() -> int:
            pipe = self._r.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_seconds, nx=True)
            return int(pipe.execute()[0])

        return int(self._guard(run))

    def get_int(self, key: str) -> int:
        value = self._guard(lambda: self._r.get(key))
        return int(value) if value is not None else 0

    def set_until(self, key: str, until_epoch: float) -> None:
        ttl = max(int(until_epoch - time.time()) + 1, 1)
        self._guard(lambda: self._r.set(key, str(until_epoch), ex=ttl))

    def get_until(self, key: str) -> float | None:
        value = self._guard(lambda: self._r.get(key))
        if value is None:
            return None
        until = float(value)
        return until if until > time.time() else None


def next_daily_reset(now: datetime, timezone_name: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone_name))
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(UTC)


class QuotaTracker:
    def __init__(
        self,
        store: QuotaStore,
        limits: dict[str, QuotaLimits],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._limits = limits  # keyed by route.key, falling back to provider name
        self._now = now

    def limits_for(self, route: ModelRoute) -> QuotaLimits:
        return self._limits.get(route.key) or self._limits.get(route.provider) or QuotaLimits()

    def _day_key(self, route: ModelRoute, now: datetime) -> str:
        tz = self.limits_for(route).reset_timezone
        return f"{KEY_PREFIX}rpd:{route.key}:{now.astimezone(ZoneInfo(tz)).date().isoformat()}"

    def _minute_key(self, route: ModelRoute, now: datetime) -> str:
        return f"{KEY_PREFIX}rpm:{route.key}:{int(now.timestamp() // 60)}"

    def blocked_until(self, route: ModelRoute) -> datetime | None:
        """When the route becomes usable again, or None if usable now."""
        now = self._now()
        candidates = [
            self._store.get_until(f"{KEY_PREFIX}block:{route.key}"),
            self._store.get_until(f"{KEY_PREFIX}block:{route.provider}"),
        ]
        until = max((c for c in candidates if c), default=None)
        if until:
            return datetime.fromtimestamp(until, tz=UTC)
        limits = self.limits_for(route)
        if limits.rpd is not None and self._store.get_int(self._day_key(route, now)) >= limits.rpd:
            return next_daily_reset(now, limits.reset_timezone)
        if (
            limits.rpm is not None
            and self._store.get_int(self._minute_key(route, now)) >= limits.rpm
        ):
            return now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return None

    def reserve(self, route: ModelRoute) -> bool:
        """Count one request against the route's caps. False if a cap would be exceeded."""
        if self.blocked_until(route) is not None:
            return False
        now = self._now()
        limits = self.limits_for(route)
        if limits.rpm is not None:
            self._store.incr(self._minute_key(route, now), 120)
        if limits.rpd is not None:
            self._store.incr(self._day_key(route, now), 2 * 86400)
        return True

    def bump(self, name: str, ttl_seconds: int) -> int:
        """Increment an auxiliary counter (429 streaks, circuit-breaker failures)."""
        return self._store.incr(f"{KEY_PREFIX}{name}", ttl_seconds)

    def block_route(self, route: ModelRoute, until: datetime) -> None:
        self._store.set_until(f"{KEY_PREFIX}block:{route.key}", until.timestamp())

    def block_provider(self, provider: str, until: datetime) -> None:
        self._store.set_until(f"{KEY_PREFIX}block:{provider}", until.timestamp())

    def quota_reset_for(self, route: ModelRoute) -> datetime:
        return next_daily_reset(self._now(), self.limits_for(route).reset_timezone)
