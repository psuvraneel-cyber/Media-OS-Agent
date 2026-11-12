"""FastAPI application factory for Media Intelligence OS (Phase 4 Persistence)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api import __version__
from media_os_api.api import api_v1_router, health
from media_os_api.auth import (
    LoginRateLimiter,
    LoginRateLimiterProtocol,
    RedisLoginRateLimiter,
    SessionRevocationStore,
    create_redis_client,
)
from media_os_api.auth.dependencies import AuthRuntimeState
from media_os_api.config import Settings, load_settings
from media_os_api.db import (
    create_db_engine_from_settings,
    create_session_factory,
    ensure_owner_bootstrapped,
    seed_initial_brands,
    session_scope,
)
from media_os_api.middleware import RequestContextMiddleware
from media_os_api.observability import RequestTelemetryStore, configure_logging, get_request_id
from media_os_api.schemas.common import ErrorDetail, ErrorEnvelope

logger = logging.getLogger("media_os_api.app")


def _build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=get_request_id(),
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(),
        headers=headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the Media Intelligence OS FastAPI application.

    Fails closed when required runtime settings are missing or unsafe.
    """
    resolved_settings = settings if settings is not None else load_settings()
    configure_logging(
        level=resolved_settings.log_level,
        service_name=resolved_settings.service_name,
    )

    db_engine: Engine | None = None
    session_factory: sessionmaker[Session] | None = None
    if resolved_settings.resolved_database_url() is not None:
        db_engine = create_db_engine_from_settings(resolved_settings)
        session_factory = create_session_factory(db_engine)

    redis_client = None
    rate_limiter: LoginRateLimiterProtocol
    if resolved_settings.resolved_redis_url() is not None:
        redis_client = create_redis_client(
            resolved_settings.resolved_redis_url() or "",
            socket_timeout_seconds=resolved_settings.redis_socket_timeout_seconds,
        )
        rate_limiter = RedisLoginRateLimiter(
            redis_client,
            max_attempts=resolved_settings.login_rate_limit_max_attempts,
            window_seconds=resolved_settings.login_rate_limit_window_seconds,
            fail_closed_on_outage=resolved_settings.rate_limit_fail_closed_on_redis_outage,
        )
    else:
        rate_limiter = LoginRateLimiter(
            max_attempts=resolved_settings.login_rate_limit_max_attempts,
            window_seconds=resolved_settings.login_rate_limit_window_seconds,
        )

    telemetry_store = RequestTelemetryStore(max_events=100)
    auth_state = AuthRuntimeState(
        settings=resolved_settings,
        revocation_store=SessionRevocationStore(max_entries=1000),
        rate_limiter=rate_limiter,
        session_factory=session_factory,
        redis_client=redis_client,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        if session_factory is not None:
            try:
                with session_scope(session_factory) as db:
                    ensure_owner_bootstrapped(db, resolved_settings)
                    seed_initial_brands(db)
            except SQLAlchemyError as exc:
                logger.warning(
                    "db_startup_bootstrap_deferred",
                    extra={"error_category": type(exc).__name__},
                )

        logger.info(
            "api_startup",
            extra={
                "app_env": resolved_settings.app_env,
                "app_phase": resolved_settings.app_phase,
                "cookie_secure": resolved_settings.cookie_secure,
                "persistence_configured": session_factory is not None,
                "redis_configured": redis_client is not None,
            },
        )
        try:
            yield
        finally:
            if redis_client is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    redis_client.close()
            if db_engine is not None:
                db_engine.dispose()
            logger.info("api_shutdown")

    app = FastAPI(
        title="Media Intelligence OS API",
        version=__version__,
        description="Local-first autonomous media intelligence control plane (Phase 4).",
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    app.state.auth_state = auth_state
    app.state.telemetry_store = telemetry_store
    app.state.db_engine = db_engine
    app.state.session_factory = session_factory
    app.state.redis_client = redis_client

    app.add_middleware(RequestContextMiddleware, telemetry_store=telemetry_store)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", "HTTP_ERROR"))
            message = str(exc.detail.get("message", "Request failed."))
            details = exc.detail.get("details")
        else:
            code = f"HTTP_{exc.status_code}"
            message = str(exc.detail) if exc.detail else "Request failed."
            details = None
        return _build_error_response(
            status_code=exc.status_code,
            code=code,
            message=message,
            details=details,
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        safe_details = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "Invalid field")),
                "type": str(err.get("type", "value_error")),
            }
            for err in exc.errors()
        ]
        return _build_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_ERROR",
            message="Request payload validation failed.",
            details=safe_details,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", extra={"error_category": type(exc).__name__})
        return _build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="An internal error occurred.",
        )

    app.include_router(health.router)
    app.include_router(api_v1_router)
    return app
