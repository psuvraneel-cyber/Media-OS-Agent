"""Single-owner authentication endpoints (`login`, `logout`, `me`) with PostgreSQL & Redis."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from media_os_api.auth import (
    RateLimiterUnavailableError,
    SessionPayload,
    authenticate_owner_from_db,
    issue_session_token,
    record_issued_session,
    require_csrf,
    require_session,
    revoke_persisted_session,
    validate_request_origin,
    verify_owner_credentials,
)
from media_os_api.auth.dependencies import AuthRuntimeState, get_auth_state
from media_os_api.db.seed import ensure_owner_bootstrapped
from media_os_api.db.session import session_scope
from media_os_api.observability import get_request_id
from media_os_api.schemas.auth import LoginRequest, LogoutResponse, SessionUserResponse
from media_os_api.services.audit_service import record_audit_event

router = APIRouter(prefix="/auth", tags=["auth"])


def _extract_client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post("/login", response_model=SessionUserResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_state: Annotated[AuthRuntimeState, Depends(get_auth_state)],
) -> SessionUserResponse:
    """Authenticate single owner and set signed HttpOnly session + CSRF cookies."""
    settings = auth_state.settings
    validate_request_origin(request, settings)

    client_key = _extract_client_key(request)
    try:
        rate_decision = auth_state.rate_limiter.check(client_key, payload.username)
    except RateLimiterUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RATE_LIMITER_UNAVAILABLE",
                "message": "Authentication rate limiter backend is temporarily unavailable.",
            },
        ) from exc

    if not rate_decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "Too many failed login attempts. Retry later.",
            },
            headers={"Retry-After": str(rate_decision.retry_after_seconds)},
        )

    req_id = get_request_id()

    if auth_state.session_factory is not None:
        try:
            with session_scope(auth_state.session_factory) as db:
                ensure_owner_bootstrapped(db, settings)
                owner = authenticate_owner_from_db(
                    db,
                    submitted_username=payload.username,
                    submitted_password=payload.password,
                )
                if owner is None:
                    import contextlib

                    with contextlib.suppress(RateLimiterUnavailableError):
                        auth_state.rate_limiter.record_failure(client_key, payload.username)
                    record_audit_event(
                        db,
                        actor_id="anonymous",
                        action="AUTH_LOGIN_FAILED",
                        target_type="OwnerUser",
                        target_id=payload.username.strip()[:128] or "unknown",
                        outcome="DENIED",
                        request_id=req_id,
                        metadata={"client_key": client_key},
                    )
                    # Commit the failed login AuditEvent before raising 401
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={
                            "code": "INVALID_CREDENTIALS",
                            "message": "Invalid username or password.",
                        },
                    )

                auth_state.rate_limiter.reset_success(client_key, payload.username)
                token, session_payload = issue_session_token(
                    subject=owner.username,
                    secret=settings.session_secret.get_secret_value(),
                    ttl_seconds=settings.session_ttl_seconds,
                    token_version=owner.token_version,
                )
                record_issued_session(db, owner_user_id=owner.id, payload=session_payload)
                record_audit_event(
                    db,
                    actor_id=f"owner:{owner.username}",
                    action="AUTH_LOGIN_SUCCESS",
                    target_type="AuthSession",
                    target_id=session_payload.jti,
                    outcome="SUCCESS",
                    request_id=req_id,
                    metadata={"token_version": owner.token_version},
                )
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Authentication storage is temporarily unavailable.",
                },
            ) from exc
    else:
        valid = verify_owner_credentials(
            submitted_username=payload.username,
            submitted_password=payload.password,
            configured_username=settings.owner_username,
            configured_password_hash=settings.resolved_owner_password_hash(),
        )
        if not valid:
            auth_state.rate_limiter.record_failure(client_key, payload.username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "INVALID_CREDENTIALS",
                    "message": "Invalid username or password.",
                },
            )
        auth_state.rate_limiter.reset_success(client_key, payload.username)
        token, session_payload = issue_session_token(
            subject=settings.owner_username,
            secret=settings.session_secret.get_secret_value(),
            ttl_seconds=settings.session_ttl_seconds,
        )

    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=session_payload.csrf_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )

    return SessionUserResponse(
        username=session_payload.subject,
        csrf_token=session_payload.csrf_token,
        expires_at_utc=datetime.fromtimestamp(session_payload.expires_at, tz=UTC).isoformat(),
        phase=settings.app_phase,
    )


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    auth_state: Annotated[AuthRuntimeState, Depends(get_auth_state)],
) -> LogoutResponse:
    """Revoke current session token persistently and expire session + CSRF cookies."""
    settings = auth_state.settings

    if auth_state.session_factory is not None:
        try:
            with session_scope(auth_state.session_factory) as db:
                revoke_persisted_session(db, jti=session.jti, reason="logout")
                record_audit_event(
                    db,
                    actor_id=f"owner:{session.subject}",
                    action="AUTH_LOGOUT",
                    target_type="AuthSession",
                    target_id=session.jti,
                    outcome="SUCCESS",
                    request_id=get_request_id(),
                )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Unable to persist session revocation; try again.",
                },
            ) from exc

    auth_state.revocation_store.revoke(session.jti, session.expires_at)

    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )
    return LogoutResponse(message="Signed out cleanly.")


@router.get("/me", response_model=SessionUserResponse)
def session_me(
    session: Annotated[SessionPayload, Depends(require_session)],
    auth_state: Annotated[AuthRuntimeState, Depends(get_auth_state)],
) -> SessionUserResponse:
    """Return authenticated owner metadata and bound CSRF token for browser requests."""
    return SessionUserResponse(
        username=session.subject,
        csrf_token=session.csrf_token,
        expires_at_utc=datetime.fromtimestamp(session.expires_at, tz=UTC).isoformat(),
        phase=auth_state.settings.app_phase,
    )
