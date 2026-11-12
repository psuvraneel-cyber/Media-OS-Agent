"""Task error classification, payload secret guards, and bounded jittered backoff."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from media_os_api.observability.redaction import is_sensitive_key, redact_text


class JobPayloadSecurityError(ValueError):
    """Raised when a job payload attempts to store secrets, tokens, or passwords."""


class TaskExecutionError(RuntimeError):
    """Base error for background task execution failures."""


class RetryableTaskError(TaskExecutionError):
    """Transient failure that may be retried within bounded maximum attempts."""

    def __init__(self, message: str, *, code: str = "TRANSIENT_FAILURE") -> None:
        super().__init__(message)
        self.code = code


class PermanentTaskError(TaskExecutionError):
    """Non-retryable failure that transitions directly to FAILED_FINAL."""

    def __init__(self, message: str, *, code: str = "PERMANENT_FAILURE") -> None:
        super().__init__(message)
        self.code = code


class PolicyRefusalError(PermanentTaskError):
    """Policy violation that must never be retried or bypassed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="POLICY_REFUSAL")


class AuthenticationTaskError(PermanentTaskError):
    """Authentication/credential rejection that must not retry indefinitely."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="AUTHENTICATION_FAILURE")


class JobIdempotencyConflictError(ValueError):
    """Raised when the same idempotency_key is reused for a different task or payload."""


@dataclass(frozen=True)
class ErrorClassification:
    """Deterministic classification result for task and outbox exceptions."""

    retryable: bool
    code: str
    sanitized_message: str


def _walk_keys(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key_str = str(k)
            found.append(key_str)
            found.extend(_walk_keys(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found.extend(_walk_keys(item))
    return found


def validate_safe_task_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Ensure task payloads are JSON-safe mappings free of secret or credential fields."""
    for key in _walk_keys(payload):
        if is_sensitive_key(key):
            raise JobPayloadSecurityError(
                f"Task payload must not contain sensitive credential field '{key}'."
            )
    return dict(payload)


def classify_exception(exc: Exception) -> ErrorClassification:
    """Classify an exception into retryable vs permanent with sanitized messaging."""
    raw_msg = redact_text(str(exc) or type(exc).__name__)[:500]

    if isinstance(exc, PolicyRefusalError):
        return ErrorClassification(retryable=False, code=exc.code, sanitized_message=raw_msg)
    if isinstance(exc, AuthenticationTaskError):
        return ErrorClassification(retryable=False, code=exc.code, sanitized_message=raw_msg)
    if isinstance(exc, PermanentTaskError):
        return ErrorClassification(retryable=False, code=exc.code, sanitized_message=raw_msg)
    if isinstance(exc, RetryableTaskError):
        return ErrorClassification(retryable=True, code=exc.code, sanitized_message=raw_msg)
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ErrorClassification(
            retryable=True,
            code="TRANSIENT_IO_ERROR",
            sanitized_message=raw_msg,
        )
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return ErrorClassification(
            retryable=False,
            code="INVALID_PAYLOAD",
            sanitized_message=raw_msg,
        )

    # Check SQLAlchemy / Redis transient errors lazily by module/class name
    type_name = type(exc).__name__
    if type_name in {"OperationalError", "InterfaceError", "DisconnectionError", "TimeoutError"}:
        return ErrorClassification(
            retryable=True,
            code="TRANSIENT_DEPENDENCY_ERROR",
            sanitized_message=raw_msg,
        )
    if type_name in {"IntegrityError", "DataError", "ProgrammingError"}:
        return ErrorClassification(
            retryable=False,
            code="PERMANENT_DB_ERROR",
            sanitized_message=raw_msg,
        )

    return ErrorClassification(
        retryable=False,
        code=f"UNCLASSIFIED_{type_name.upper()}"[:64],
        sanitized_message=raw_msg,
    )


def compute_backoff_seconds(
    attempt: int,
    *,
    base_seconds: float = 2.0,
    max_seconds: float = 300.0,
    jitter_ratio: float = 0.2,
) -> float:
    """Compute bounded exponential backoff with symmetric positive jitter."""
    exponent = max(0, attempt - 1)
    raw = min(max_seconds, base_seconds * (2.0**exponent))
    jitter_span = raw * max(0.0, min(jitter_ratio, 0.5))
    jitter = random.SystemRandom().uniform(0.0, jitter_span)
    return round(min(max_seconds, raw + jitter), 3)
