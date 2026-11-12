"""Authentication, password verification, session tokens, rate limiting, and CSRF."""

from media_os_api.auth.dependencies import (
    AuthRuntimeState,
    get_auth_state,
    require_csrf,
    require_session,
    validate_request_origin,
)
from media_os_api.auth.passwords import hash_password, verify_owner_credentials, verify_password
from media_os_api.auth.persistence import (
    OwnerIdentityError,
    authenticate_owner_from_db,
    cleanup_expired_auth_sessions,
    get_owner_by_username,
    record_issued_session,
    revoke_persisted_session,
    rotate_owner_password,
    set_owner_active_status,
    verify_persisted_session,
)
from media_os_api.auth.rate_limit import (
    LoginRateLimiter,
    LoginRateLimiterProtocol,
    RateLimiterUnavailableError,
    RateLimitResult,
    RedisLoginRateLimiter,
    check_redis_health,
    create_redis_client,
)
from media_os_api.auth.tokens import (
    SessionPayload,
    SessionRevocationStore,
    SessionTokenError,
    issue_session_token,
    verify_session_token,
)

__all__ = [
    "AuthRuntimeState",
    "LoginRateLimiter",
    "LoginRateLimiterProtocol",
    "OwnerIdentityError",
    "RateLimitResult",
    "RateLimiterUnavailableError",
    "RedisLoginRateLimiter",
    "SessionPayload",
    "SessionRevocationStore",
    "SessionTokenError",
    "authenticate_owner_from_db",
    "check_redis_health",
    "cleanup_expired_auth_sessions",
    "create_redis_client",
    "get_auth_state",
    "get_owner_by_username",
    "hash_password",
    "issue_session_token",
    "record_issued_session",
    "require_csrf",
    "require_session",
    "revoke_persisted_session",
    "rotate_owner_password",
    "set_owner_active_status",
    "validate_request_origin",
    "verify_owner_credentials",
    "verify_password",
    "verify_persisted_session",
    "verify_session_token",
]
