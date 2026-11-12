"""Bounded in-memory telemetry ring buffer for sanitized Phase 3 runtime logs."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from media_os_api.observability.redaction import redact_text


@dataclass(frozen=True)
class RequestTelemetryEvent:
    """Non-secret summary of a single HTTP request handled during current uptime."""

    timestamp_utc: str
    request_id: str
    method: str
    route: str
    status_code: int
    duration_ms: float
    error_category: str | None = None


class RequestTelemetryStore:
    """Thread-safe ring buffer retaining up to `max_events` sanitized events."""

    def __init__(self, max_events: int = 100) -> None:
        self._events: deque[RequestTelemetryEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def record(
        self,
        *,
        request_id: str,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        error_category: str | None = None,
    ) -> None:
        event = RequestTelemetryEvent(
            timestamp_utc=datetime.now(UTC).isoformat(),
            request_id=request_id,
            method=method.upper(),
            route=redact_text(route.split("?", 1)[0]),
            status_code=status_code,
            duration_ms=round(duration_ms, 2),
            error_category=redact_text(error_category) if error_category else None,
        )
        with self._lock:
            self._events.appendleft(event)

    def recent(self, limit: int = 50) -> list[RequestTelemetryEvent]:
        capped = max(1, min(limit, 100))
        with self._lock:
            return list(self._events)[:capped]
