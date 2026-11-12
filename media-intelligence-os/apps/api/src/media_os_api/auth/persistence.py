"""PostgreSQL-backed single-owner identity, persistent session revocation, and rotation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.orm import Session

from media_os_api.auth.passwords import hash_password, verify_owner_credentials
from media_os_api.auth.tokens import SessionPayload, SessionTokenError
from media_os_api.db.models import AuthSession, OwnerUser
from media_os_api.db.normalization import compute_record_hash
from media_os_api.services.audit_service import record_audit_event


class OwnerIdentityError(ValueError):
    """Raised when owner identity operations fail validation or lookup."""


def get_owner_by_username(session: Session, username: str) -> OwnerUser | None:
    normalized = username.strip().lower()
    return session.execute(
        select(OwnerUser).where(func.lower(OwnerUser.username) == normalized)
    ).scalar_one_or_none()


def authenticate_owner_from_db(
    session: Session,
    *,
    submitted_username: str,
    submitted_password: str,
) -> OwnerUser | None:
    """Authenticate single owner against PostgreSQL OwnerUser preserving constant-time checks."""
    owner = get_owner_by_username(session, submitted_username)
    if owner is None:
        verify_owner_credentials(
            submitted_username=submitted_username,
            submitted_password=submitted_password,
            configured_username="__nonexistent_owner__",
            configured_password_hash="",
        )
        return None

    valid = verify_owner_credentials(
        submitted_username=submitted_username,
        submitted_password=submitted_password,
        configured_username=owner.username,
        configured_password_hash=owner.password_hash,
    )
    if not valid or not owner.is_active:
        return None

    owner.last_login_at = datetime.now(UTC)
    session.flush()
    return owner


def record_issued_session(
    session: Session,
    *,
    owner_user_id: uuid.UUID,
    payload: SessionPayload,
) -> AuthSession:
    """Persist issued session metadata keyed by opaque `jti` (never stores raw cookie token)."""
    auth_session = AuthSession(
        owner_user_id=owner_user_id,
        jti=payload.jti,
        issued_at=datetime.fromtimestamp(payload.issued_at, tz=UTC),
        expires_at=datetime.fromtimestamp(payload.expires_at, tz=UTC),
        revoked_at=None,
        revocation_reason=None,
    )
    session.add(auth_session)
    session.flush()
    return auth_session


def verify_persisted_session(
    session: Session,
    payload: SessionPayload,
    *,
    now: datetime | None = None,
) -> OwnerUser:
    """Validate that a signed session exists, is unrevoked, unexpired, and matches active owner."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    row = session.execute(
        select(AuthSession, OwnerUser)
        .join(OwnerUser, AuthSession.owner_user_id == OwnerUser.id)
        .where(AuthSession.jti == payload.jti)
    ).first()
    if row is None:
        raise SessionTokenError("Session record does not exist or has expired.")

    auth_session: AuthSession = row[0]
    owner: OwnerUser = row[1]
    if auth_session.revoked_at is not None:
        raise SessionTokenError("Session token has been revoked.")
    if auth_session.expires_at <= current:
        raise SessionTokenError("Session has expired.")
    if not owner.is_active:
        raise SessionTokenError("Owner account is inactive.")
    if owner.token_version != payload.token_version:
        raise SessionTokenError("Session invalidated by owner credential update.")
    if owner.username.strip().lower() != payload.subject.strip().lower():
        raise SessionTokenError("Session subject mismatch.")

    return owner


def revoke_persisted_session(
    session: Session,
    *,
    jti: str,
    reason: str = "logout",
    now: datetime | None = None,
) -> bool:
    """Persistently revoke an AuthSession by `jti`."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    auth_session = session.execute(
        select(AuthSession).where(AuthSession.jti == jti).with_for_update()
    ).scalar_one_or_none()
    if auth_session is None:
        return False
    if auth_session.revoked_at is None:
        auth_session.revoked_at = current
        auth_session.revocation_reason = reason[:64]
        session.flush()
    return True


def rotate_owner_password(
    session: Session,
    *,
    username: str,
    new_password: str,
    actor_id: str = "cli:rotate_password",
    request_id: str = "-",
    now: datetime | None = None,
) -> OwnerUser:
    """Deliberately rotate the owner's Argon2id password and revoke all active sessions."""
    if not new_password or len(new_password) < 8:
        raise OwnerIdentityError("New password must be at least 8 characters long.")
    owner = get_owner_by_username(session, username)
    if owner is None:
        raise OwnerIdentityError(f"Owner account '{username}' not found in database.")

    current = (now or datetime.now(UTC)).astimezone(UTC)
    before_hash = compute_record_hash(
        {
            "username": owner.username,
            "token_version": owner.token_version,
            "password_changed_at": owner.password_changed_at.isoformat(),
        }
    )

    owner.password_hash = hash_password(new_password)
    owner.password_changed_at = current
    owner.token_version += 1

    revoke_result = cast(
        CursorResult[Any],
        session.execute(
            update(AuthSession)
            .where(
                AuthSession.owner_user_id == owner.id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=current, revocation_reason="password_rotated")
        ),
    )
    revoked_count = int(revoke_result.rowcount or 0)
    session.flush()

    after_hash = compute_record_hash(
        {
            "username": owner.username,
            "token_version": owner.token_version,
            "password_changed_at": owner.password_changed_at.isoformat(),
        }
    )
    record_audit_event(
        session,
        actor_id=actor_id,
        action="OWNER_PASSWORD_ROTATED",
        target_type="OwnerUser",
        target_id=str(owner.id),
        outcome="SUCCESS",
        request_id=request_id,
        before_hash=before_hash,
        after_hash=after_hash,
        metadata={
            "username": owner.username,
            "new_token_version": owner.token_version,
            "revoked_sessions_count": revoked_count,
        },
        now=current,
    )
    return owner


def set_owner_active_status(
    session: Session,
    *,
    username: str,
    is_active: bool,
    actor_id: str = "cli:owner_status",
    request_id: str = "-",
    now: datetime | None = None,
) -> OwnerUser:
    """Enable or disable the single owner account, revoking sessions on deactivation."""
    owner = get_owner_by_username(session, username)
    if owner is None:
        raise OwnerIdentityError(f"Owner account '{username}' not found in database.")

    current = (now or datetime.now(UTC)).astimezone(UTC)
    owner.is_active = is_active
    revoked_count = 0
    if not is_active:
        owner.token_version += 1
        revoke_result = cast(
            CursorResult[Any],
            session.execute(
                update(AuthSession)
                .where(
                    AuthSession.owner_user_id == owner.id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=current, revocation_reason="owner_deactivated")
            ),
        )
        revoked_count = int(revoke_result.rowcount or 0)
    session.flush()

    record_audit_event(
        session,
        actor_id=actor_id,
        action="OWNER_ACTIVE_STATUS_CHANGED",
        target_type="OwnerUser",
        target_id=str(owner.id),
        outcome="SUCCESS",
        request_id=request_id,
        metadata={
            "username": owner.username,
            "is_active": is_active,
            "revoked_sessions_count": revoked_count,
        },
        now=current,
    )
    return owner


def cleanup_expired_auth_sessions(session: Session, *, now: datetime | None = None) -> int:
    """Safely delete ONLY expired AuthSession rows (`expires_at <= now`).

    Unexpired revoked sessions are preserved until their expiration timestamp passes.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    result = cast(
        CursorResult[Any],
        session.execute(delete(AuthSession).where(AuthSession.expires_at <= current)),
    )
    deleted = int(result.rowcount or 0)
    session.flush()
    return deleted
