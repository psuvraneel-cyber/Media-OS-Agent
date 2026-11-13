"""AI adapter errors mapped onto the durable job ledger's retry classes."""

from __future__ import annotations

from datetime import datetime

from media_os_api.jobs.errors import RetryableTaskError


class AllProvidersExhausted(RetryableTaskError):
    """Every route for the task is out of free quota; the job should resume after the reset."""

    def __init__(self, message: str, *, resume_at: datetime | None) -> None:
        super().__init__(message, code="AI_QUOTA_EXHAUSTED")
        self.resume_at = resume_at


class AIUnavailable(RetryableTaskError):
    """No route produced a valid result for reasons other than quota (outages, bad output)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="AI_UNAVAILABLE")


class InvalidModelOutput(ValueError):
    """The provider answered but the content was missing, blocked, or not valid for the schema."""

    def __init__(self, message: str, *, code: str = "INVALID_OUTPUT") -> None:
        super().__init__(message)
        self.code = code
