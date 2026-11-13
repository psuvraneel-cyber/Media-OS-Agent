"""Fetch error types that map directly onto the durable job ledger's retry classification."""

from __future__ import annotations

from datetime import timedelta

from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError


class RetryableFetchError(RetryableTaskError):
    """Transient fetch failure (timeouts, 5xx, 429, rate-limiter outage)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "FETCH_TRANSIENT",
        status_code: int | None = None,
        retry_after: timedelta | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code
        self.retry_after = retry_after


class PermanentFetchError(PermanentTaskError):
    """Fetch failure that retrying cannot fix (4xx, oversize body, malformed document)."""

    def __init__(
        self, message: str, *, code: str = "FETCH_PERMANENT", status_code: int | None = None
    ) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code


class UnsafeUrlError(PermanentFetchError):
    """URL rejected by scheme/host allowlist or SSRF address validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNSAFE_URL")


class ResponseTooLargeError(PermanentFetchError):
    """Response body exceeded the per-source byte cap."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="RESPONSE_TOO_LARGE")
