"""Hardened outbound HTTP layer shared by all research source fetchers."""

from __future__ import annotations

from media_os_api.research.http.client import HttpResult, SafeHttpClient
from media_os_api.research.http.errors import (
    PermanentFetchError,
    ResponseTooLargeError,
    RetryableFetchError,
    UnsafeUrlError,
)
from media_os_api.research.http.policy import DEFAULT_USER_AGENT, HostPolicy
from media_os_api.research.http.ratelimit import (
    InMemoryMinIntervalLimiter,
    RateLimiter,
    RedisMinIntervalLimiter,
    build_redis_rate_limiter,
)
from media_os_api.research.http.retry import RetryPolicy, classify_status, parse_retry_after
from media_os_api.research.http.ssrf import is_public_address, system_resolver, validate_url

__all__ = [
    "DEFAULT_USER_AGENT",
    "HostPolicy",
    "HttpResult",
    "InMemoryMinIntervalLimiter",
    "PermanentFetchError",
    "RateLimiter",
    "RedisMinIntervalLimiter",
    "ResponseTooLargeError",
    "RetryPolicy",
    "RetryableFetchError",
    "SafeHttpClient",
    "UnsafeUrlError",
    "build_redis_rate_limiter",
    "classify_status",
    "is_public_address",
    "parse_retry_after",
    "system_resolver",
    "validate_url",
]
