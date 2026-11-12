"""Structured JSON logging with request correlation IDs and secret scrubbing."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from media_os_api.observability.redaction import redact_mapping, redact_text

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

STANDARD_LOG_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def set_request_id(request_id: str) -> contextvars.Token[str]:
    """Bind the active request correlation ID into context-local storage."""
    return _request_id_ctx.set(request_id)


def get_request_id() -> str:
    """Return the active request correlation ID or '-' outside a request."""
    return _request_id_ctx.get()


def clear_request_id(token: contextvars.Token[str]) -> None:
    """Restore the previous request correlation ID context token."""
    _request_id_ctx.reset(token)


class RedactingFilter(logging.Filter):
    """Attach correlation ID and scrub log message/extra attributes before emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with safe structured metadata."""

    def __init__(self, service_name: str = "media-os-api") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "request_id": getattr(record, "request_id", get_request_id()),
            "message": redact_text(record.getMessage()),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in STANDARD_LOG_ATTRS and key != "request_id" and not key.startswith("_")
        }
        if extras:
            payload.update(redact_mapping(extras))
        if record.exc_info:
            payload["error_category"] = (
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            )
        return json.dumps(payload, default=str, ensure_ascii=True)


def configure_logging(level: str = "INFO", service_name: str = "media-os-api") -> logging.Logger:
    """Configure root JSON structured logging idempotently and return app logger."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name=service_name))
    handler.addFilter(RedactingFilter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    for noisy in ("uvicorn.access",):
        access_logger = logging.getLogger(noisy)
        access_logger.handlers.clear()
        access_logger.propagate = False
        access_logger.disabled = True

    return logging.getLogger("media_os_api")
