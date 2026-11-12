"""Log and telemetry redaction helpers ensuring secrets never leak to logs or API output."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
        "x-api-key",
        "api-key",
        "apikey",
        "password",
        "password_hash",
        "passphrase",
        "session_secret",
        "csrf_token",
        "access_token",
        "refresh_token",
        "secret",
        "token",
        "media_os_session",
        "media_os_csrf",
        "database_url",
        "redis_url",
        "celery_broker_url",
        "celery_result_backend",
    }
)

SENSITIVE_KEY_SUFFIXES = (
    "_password",
    "_password_hash",
    "_secret",
    "_token",
    "_api_key",
    "_key",
    "_dsn",
)

TEXT_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"\$argon2(?:id|i|d)\$[^\s\"';,]+"), "[REDACTED_ARGON2_HASH]"),
    (re.compile("AI" + r"za[0-9A-Za-z_-]{15,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile("sk-" + r"[A-Za-z0-9]{16,}"), "[REDACTED_API_KEY]"),
    (re.compile("pos_" + r"[A-Za-z0-9]{12,}"), "[REDACTED_POSTIZ_TOKEN]"),
    (
        re.compile(
            r"(?i)\b((?:postgres(?:ql)?(?:\+\w+)?|redis(?:s)?|amqp(?:s)?)://[^:\s/]*:)([^@\s]+)(@)"
        ),
        r"\1[REDACTED]\3",
    ),
    (
        re.compile(r"(?i)\b(media_os_session|media_os_csrf|session|csrf)=([^;\s,&]+)"),
        r"\1=[REDACTED]",
    ),
)


def is_sensitive_key(key: str) -> bool:
    """Return True when a field or header name denotes a credential or session artifact."""
    lowered = key.strip().lower()
    if lowered in SENSITIVE_EXACT_KEYS:
        return True
    return lowered.endswith(SENSITIVE_KEY_SUFFIXES)


def redact_text(value: str) -> str:
    """Scrub embedded tokens, credentials, and cookies from arbitrary log strings."""
    cleaned = value
    for pattern, replacement in TEXT_REDACTION_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively mask sensitive fields in nested mappings/lists."""
    if key is not None and is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied, scrubbed dictionary safe for structured logging."""
    return {str(k): redact_value(v, key=str(k)) for k, v in mapping.items()}
