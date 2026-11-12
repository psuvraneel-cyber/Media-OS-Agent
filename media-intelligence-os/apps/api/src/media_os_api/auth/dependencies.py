"""FastAPI authentication, CSRF, and origin verification dependencies."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.auth.persistence import verify_persisted_session
from media_os_api.auth.rate_limit import LoginRateLimiterProtocol
from media_os_api.auth.tokens import (
    SessionPayload,
    SessionRevocationStore,
    SessionTokenError,
    verify_session_token,
)
from media_os_api.config import Settings
from media_os_api.db.session import session_scope


@dataclass
class AuthRuntimeState:
    """Runtime authentication collaborators attached to `app.state`."""

    settings: Settings
    revocation_store: SessionRevocationStore
    rate_limiter: LoginRateLimiterProtocol
    session_factory: sessionmaker[Session] | None = None
    redis_client: Redis | None = None


def get_auth_state(request: Request) -> AuthRuntimeState:
    """Retrieve initialized `AuthRuntimeState` from FastAPI app state."""
    state: AuthRuntimeState | None = getattr(request.app.state, "auth_state", None)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SERVICE_NOT_READY",
                "message": "Authentication runtime is not initialized.",
            },
        )
    return state


def validate_request_origin(request: Request, settings: Settings) -> None:
    """Reject cross-origin unsafe browser requests whose Origin header is outside allowlist."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    cleaned = origin.strip().rstrip("/")
    if cleaned not in settings.cors_allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORIGIN_FORBIDDEN",
                "message": "Origin is not permitted for state-changing requests.",
            },
        )


def require_session(
    request: Request,
    auth_state: Annotated[AuthRuntimeState, Depends(get_auth_state)],
) -> SessionPayload:
    """Enforce a valid, non-expired, non-revoked HttpOnly session cookie."""
    token = request.cookies.get(auth_state.settings.cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication required.",
            },
        )
    try:
        payload = verify_session_token(
            token,
            secret=auth_state.settings.session_secret.get_secret_value(),
            revocation_store=auth_state.revocation_store,
        )
    except SessionTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "SESSION_INVALID",
                "message": "Session is invalid or expired.",
            },
        ) from exc

    if auth_state.session_factory is not None:
        try:
            with session_scope(auth_state.session_factory) as db:
                verify_persisted_session(db, payload)
        except SessionTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "SESSION_INVALID",
                    "message": "Session is invalid or expired.",
                },
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Session verification storage is temporarily unavailable.",
                },
            ) from exc

    return payload


def require_csrf(
    request: Request,
    session: Annotated[SessionPayload, Depends(require_session)],
    auth_state: Annotated[AuthRuntimeState, Depends(get_auth_state)],
) -> SessionPayload:
    """Enforce double-submit CSRF cookie + signed claim + header verification."""
    validate_request_origin(request, auth_state.settings)
    csrf_cookie = request.cookies.get(auth_state.settings.csrf_cookie_name)
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_cookie or not csrf_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CSRF_REQUIRED",
                "message": "Missing CSRF verification token.",
            },
        )

    cookie_matches_session = hmac.compare_digest(
        csrf_cookie.encode("utf-8"),
        session.csrf_token.encode("utf-8"),
    )
    header_matches_session = hmac.compare_digest(
        csrf_header.encode("utf-8"),
        session.csrf_token.encode("utf-8"),
    )
    if not (cookie_matches_session and header_matches_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CSRF_INVALID",
                "message": "CSRF token validation failed.",
            },
        )
    return session
