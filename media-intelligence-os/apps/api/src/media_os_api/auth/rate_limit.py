"""Bounded sliding-window rate limiter with Redis primary and fail-safe fallback."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("media_os_api.rate_limit")


class RateLimiterUnavailableError(RuntimeError):
    """Raised when Redis is unavailable and fail-closed outage policy is enabled."""


@dataclass(frozen=True)
class RateLimitResult:
    """Decision from login rate limiter before attempting credential verification."""

    allowed: bool
    retry_after_seconds: int = 0
    backend: str = "memory"


class LoginRateLimiterProtocol(Protocol):
    """Common protocol for in-memory and Redis-backed login rate limiters."""

    def check(
        self, client_key: str, username: str, *, now: float | None = None
    ) -> RateLimitResult: ...

    def record_failure(
        self, client_key: str, username: str, *, now: float | None = None
    ) -> None: ...

    def reset_success(self, client_key: str, username: str) -> None: ...


class LoginRateLimiter:
    """Bounded in-memory sliding-window limiter keyed by client host and username bucket."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: int = 300,
        max_keys: int = 256,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune_bucket(self, key: str, now: float) -> deque[float]:
        cutoff = now - self._window_seconds
        bucket = self._buckets.get(key)
        if bucket is None:
            return deque()
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            self._buckets.pop(key, None)
            return deque()
        self._buckets.move_to_end(key)
        return bucket

    def _bucket_keys(self, client_key: str, username: str) -> tuple[str, str]:
        normalized_ip = (client_key or "unknown").strip().lower()
        normalized_user = (username or "unknown").strip().lower()
        return (f"ip:{normalized_ip}", f"user:{normalized_user}")

    def check(self, client_key: str, username: str, *, now: float | None = None) -> RateLimitResult:
        current = time.monotonic() if now is None else now
        keys = self._bucket_keys(client_key, username)
        with self._lock:
            worst_retry = 0
            for key in keys:
                bucket = self._prune_bucket(key, current)
                if len(bucket) >= self._max_attempts:
                    retry_after = int(max(1.0, (bucket[0] + self._window_seconds) - current))
                    worst_retry = max(worst_retry, retry_after)
            if worst_retry > 0:
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=worst_retry,
                    backend="memory",
                )
            return RateLimitResult(allowed=True, retry_after_seconds=0, backend="memory")

    def record_failure(self, client_key: str, username: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        keys = self._bucket_keys(client_key, username)
        with self._lock:
            for key in keys:
                bucket = self._prune_bucket(key, current)
                bucket.append(current)
                while key not in self._buckets and len(self._buckets) >= self._max_keys:
                    self._buckets.popitem(last=False)
                self._buckets[key] = bucket
                self._buckets.move_to_end(key)

    def reset_success(self, client_key: str, username: str) -> None:
        keys = self._bucket_keys(client_key, username)
        with self._lock:
            for key in keys:
                self._buckets.pop(key, None)


class RedisLoginRateLimiter:
    """Redis-backed sliding-window login rate limiter with bounded outage protection.

    Outage behavior:
    - When `fail_closed_on_outage=False` (default for single-owner workstation availability),
      failures are mirrored into the bounded in-memory `LoginRateLimiter` fallback so a Redis
      outage never disables brute-force protection.
    - When `fail_closed_on_outage=True`, Redis unavailability raises `RateLimiterUnavailableError`
      (mapped to HTTP 503) instead of allowing login attempts.
    """

    def __init__(
        self,
        redis_client: Redis,
        *,
        max_attempts: int = 5,
        window_seconds: int = 300,
        fail_closed_on_outage: bool = False,
        key_prefix: str = "media_os:rl:login",
    ) -> None:
        self._redis = redis_client
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._fail_closed = fail_closed_on_outage
        self._prefix = key_prefix
        self._fallback = LoginRateLimiter(
            max_attempts=max_attempts,
            window_seconds=window_seconds,
            max_keys=256,
        )

    def _redis_keys(self, client_key: str, username: str) -> tuple[str, str]:
        normalized_ip = (client_key or "unknown").strip().lower()
        normalized_user = (username or "unknown").strip().lower()
        return (
            f"{self._prefix}:ip:{normalized_ip}",
            f"{self._prefix}:user:{normalized_user}",
        )

    def check(self, client_key: str, username: str, *, now: float | None = None) -> RateLimitResult:
        current_wall = time.time() if now is None else now
        cutoff = current_wall - self._window_seconds
        keys = self._redis_keys(client_key, username)

        try:
            worst_retry = 0
            pipe = self._redis.pipeline()
            for key in keys:
                pipe.zremrangebyscore(key, "-inf", cutoff)
                pipe.zcard(key)
                pipe.zrange(key, 0, 0, withscores=True)
            results = pipe.execute()

            for idx in range(len(keys)):
                count = int(results[idx * 3 + 1])
                oldest = results[idx * 3 + 2]
                if count >= self._max_attempts:
                    if oldest:
                        oldest_ts = float(oldest[0][1])
                        retry_after = int(
                            max(1.0, (oldest_ts + self._window_seconds) - current_wall)
                        )
                    else:
                        retry_after = self._window_seconds
                    worst_retry = max(worst_retry, retry_after)

            if worst_retry > 0:
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=worst_retry,
                    backend="redis",
                )
            # Also check local fallback in case Redis recovered mid-window after a brief outage
            fallback_decision = self._fallback.check(client_key, username)
            if not fallback_decision.allowed:
                return fallback_decision
            return RateLimitResult(allowed=True, retry_after_seconds=0, backend="redis")
        except RedisError as exc:
            logger.warning(
                "redis_rate_limiter_outage",
                extra={
                    "operation": "check",
                    "fail_closed": self._fail_closed,
                    "error_category": type(exc).__name__,
                },
            )
            if self._fail_closed:
                raise RateLimiterUnavailableError(
                    "Login rate limiter backend is unavailable."
                ) from exc
            return self._fallback.check(client_key, username)

    def record_failure(self, client_key: str, username: str, *, now: float | None = None) -> None:
        current_wall = time.time() if now is None else now
        cutoff = current_wall - self._window_seconds
        keys = self._redis_keys(client_key, username)

        # Always record into bounded local fallback so mid-attack Redis outages lose zero strikes.
        self._fallback.record_failure(client_key, username)

        try:
            pipe = self._redis.pipeline()
            for key in keys:
                member = f"{current_wall:.6f}:{uuid.uuid4().hex[:8]}"
                pipe.zremrangebyscore(key, "-inf", cutoff)
                pipe.zadd(key, {member: current_wall})
                pipe.expire(key, self._window_seconds)
            pipe.execute()
        except RedisError as exc:
            logger.warning(
                "redis_rate_limiter_outage",
                extra={
                    "operation": "record_failure",
                    "fail_closed": self._fail_closed,
                    "error_category": type(exc).__name__,
                },
            )
            if self._fail_closed:
                raise RateLimiterUnavailableError(
                    "Login rate limiter backend is unavailable."
                ) from exc

    def reset_success(self, client_key: str, username: str) -> None:
        self._fallback.reset_success(client_key, username)
        keys = self._redis_keys(client_key, username)
        try:
            self._redis.delete(*keys)
        except RedisError as exc:
            logger.warning(
                "redis_rate_limiter_outage",
                extra={
                    "operation": "reset_success",
                    "fail_closed": self._fail_closed,
                    "error_category": type(exc).__name__,
                },
            )


def create_redis_client(
    redis_url: str,
    *,
    socket_timeout_seconds: float = 2.0,
) -> Redis:
    """Create a bounded Redis client with explicit connect and socket timeouts."""
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=socket_timeout_seconds,
        socket_connect_timeout=socket_timeout_seconds,
        health_check_interval=30,
    )


def check_redis_health(redis_client: Redis) -> tuple[bool, str]:
    """Ping Redis without leaking connection URLs or passwords."""
    try:
        pong = bool(redis_client.ping())
        if pong:
            return True, "Redis reachable (PING -> PONG)."
        return False, "Redis did not acknowledge PING."
    except RedisError as exc:
        logger.warning("redis_healthcheck_failed", extra={"error_category": type(exc).__name__})
        return False, "Redis connection unavailable."
