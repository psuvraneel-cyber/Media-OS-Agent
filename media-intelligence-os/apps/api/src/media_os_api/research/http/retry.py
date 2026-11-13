"""Retry classification and bounded backoff for research HTTP fetches."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from media_os_api.research.http.errors import PermanentFetchError, RetryableFetchError

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """In-run retry budget. Beyond it, failures surface to the job ledger / source backoff."""

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    # A server-requested wait longer than this is not slept in-process; it is raised instead so
    # the source registry records a backoff and the worker is released.
    max_in_run_retry_after_seconds: float = 60.0


def parse_retry_after(value: str | None, *, now: datetime) -> timedelta | None:
    """Parse `Retry-After` (delta-seconds or HTTP-date). Invalid/negative values → None."""
    if not value:
        return None
    raw = value.strip()
    if raw.isdigit():
        return timedelta(seconds=int(raw))
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = when - now
    return delta if delta > timedelta(0) else timedelta(0)


def _rate_limit_reset(headers: Mapping[str, str], now: datetime) -> timedelta | None:
    """GitHub-style `X-RateLimit-Reset` (epoch seconds) when the quota is exhausted."""
    if headers.get("x-ratelimit-remaining", "").strip() != "0":
        return None
    reset = headers.get("x-ratelimit-reset", "").strip()
    if not reset.isdigit():
        return timedelta(seconds=60)
    delta = datetime.fromtimestamp(int(reset), tz=UTC) - now
    return delta if delta > timedelta(0) else timedelta(seconds=1)


def classify_status(
    status: int, headers: Mapping[str, str], *, now: datetime
) -> RetryableFetchError | PermanentFetchError:
    """Map a non-2xx/304 HTTP status to a retryable or permanent fetch error."""
    lowered = {k.lower(): v for k, v in headers.items()}
    code = f"HTTP_{status}"
    retry_after = parse_retry_after(lowered.get("retry-after"), now=now)

    if status == 403:
        quota_reset = _rate_limit_reset(lowered, now)
        if quota_reset is not None or retry_after is not None:
            return RetryableFetchError(
                "Upstream rate limit exhausted (HTTP 403).",
                code="HTTP_403_RATE_LIMITED",
                status_code=status,
                retry_after=retry_after or quota_reset,
            )
    if status in RETRYABLE_STATUSES or 500 <= status <= 599:
        if status == 429 and retry_after is None:
            retry_after = _rate_limit_reset(lowered, now)
        return RetryableFetchError(
            f"Transient upstream response (HTTP {status}).",
            code=code,
            status_code=status,
            retry_after=retry_after,
        )
    return PermanentFetchError(
        f"Upstream rejected request (HTTP {status}).", code=code, status_code=status
    )


def backoff_delay(attempt: int, policy: RetryPolicy, *, rng: random.Random | None = None) -> float:
    """Full-jitter exponential backoff for retry number `attempt` (1-based)."""
    exponent = min(max(attempt - 1, 0), 16)
    cap = min(policy.max_delay_seconds, policy.base_delay_seconds * float(1 << exponent))
    if rng is not None:
        return rng.uniform(0, cap)
    return random.uniform(0, cap)  # noqa: S311 - jitter, not cryptography
