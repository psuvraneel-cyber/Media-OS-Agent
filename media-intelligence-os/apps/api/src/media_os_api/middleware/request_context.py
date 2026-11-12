"""Middleware adding correlation IDs, security headers, telemetry, and JSON request logs."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from media_os_api.observability.logging import clear_request_id, set_request_id
from media_os_api.observability.telemetry import RequestTelemetryStore

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
logger = logging.getLogger("media_os_api.http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach `X-Request-ID`, emit structured request logs, and set defensive HTTP headers."""

    def __init__(self, app: ASGIApp, *, telemetry_store: RequestTelemetryStore) -> None:
        super().__init__(app)
        self._telemetry_store = telemetry_store

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming_id = request.headers.get("x-request-id", "").strip()
        request_id = incoming_id if _VALID_REQUEST_ID.match(incoming_id) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()

        error_category: str | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            error_category = type(exc).__name__
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            route_path = request.url.path
            self._telemetry_store.record(
                request_id=request_id,
                method=request.method,
                route=route_path,
                status_code=status_code,
                duration_ms=duration_ms,
                error_category=error_category,
            )
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "route": route_path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                    "error_category": error_category,
                },
            )
            clear_request_id(token)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response
