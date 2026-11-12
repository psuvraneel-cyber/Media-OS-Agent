"""Unit tests for Phase 3 configuration, Argon2id/HMAC auth, rate limiting, and redaction."""

from __future__ import annotations

import json
import logging

import pytest

from media_os_api.auth import (
    LoginRateLimiter,
    SessionRevocationStore,
    SessionTokenError,
    hash_password,
    issue_session_token,
    verify_owner_credentials,
    verify_session_token,
)
from media_os_api.config import ConfigurationError, load_settings
from media_os_api.observability.logging import JsonFormatter, RedactingFilter
from media_os_api.observability.redaction import redact_mapping, redact_text

pytestmark = pytest.mark.unit


def _sample_hash() -> str:
    return hash_password("Test-Owner-Pass-9876!")


def test_load_settings_fails_closed_on_missing_or_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "OWNER_USERNAME",
        "OWNER_PASSWORD_HASH",
        "SESSION_SECRET",
        "COOKIE_SECURE",
        "CORS_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings()
    assert "Invalid application configuration" in str(exc_info.value)

    import base64

    encoded_hash = base64.b64encode(_sample_hash().encode("utf-8")).decode("ascii")
    b64_settings = load_settings(
        {
            "OWNER_USERNAME": "owner",
            "OWNER_PASSWORD_HASH_B64": encoded_hash,
            "SESSION_SECRET": "a-very-long-random-test-session-secret-0123456789",
        }
    )
    assert b64_settings.resolved_owner_password_hash().startswith("$argon2id$")

    sentinel_secret = "too-short-secret-value-xyz"
    with pytest.raises(ConfigurationError) as short_exc:
        load_settings(
            {
                "OWNER_USERNAME": "owner",
                "OWNER_PASSWORD_HASH": _sample_hash(),
                "SESSION_SECRET": sentinel_secret,
            }
        )
    # Secrets or malformed values must never be echoed back in ConfigurationError messages.
    assert sentinel_secret not in str(short_exc.value)


def test_cookie_secure_false_rejected_for_non_loopback_origin() -> None:
    with pytest.raises(ConfigurationError, match="loopback hosts"):
        load_settings(
            {
                "OWNER_USERNAME": "owner",
                "OWNER_PASSWORD_HASH": _sample_hash(),
                "SESSION_SECRET": "a-very-long-random-test-session-secret-0123456789",
                "COOKIE_SECURE": "false",
                "CORS_ALLOWED_ORIGINS": "https://dashboard.example.com",
            }
        )


def test_owner_password_verification_and_invalid_username_timing_path() -> None:
    pwd_hash = _sample_hash()
    assert verify_owner_credentials(
        submitted_username="Owner",
        submitted_password="Test-Owner-Pass-9876!",
        configured_username="owner",
        configured_password_hash=pwd_hash,
    )
    assert not verify_owner_credentials(
        submitted_username="wrong-user",
        submitted_password="Test-Owner-Pass-9876!",
        configured_username="owner",
        configured_password_hash=pwd_hash,
    )
    assert not verify_owner_credentials(
        submitted_username="owner",
        submitted_password="Wrong-Password!",
        configured_username="owner",
        configured_password_hash=pwd_hash,
    )


def test_session_token_roundtrip_tamper_expiry_and_revocation() -> None:
    secret = "0123456789abcdef0123456789abcdef0123456789abcdef"
    revocations = SessionRevocationStore(max_entries=2)

    token, payload = issue_session_token(
        subject="owner",
        secret=secret,
        ttl_seconds=300,
        now=10_000,
    )
    verified = verify_session_token(
        token,
        secret=secret,
        revocation_store=revocations,
        now=10_100,
    )
    assert verified == payload

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(SessionTokenError, match="signature"):
        verify_session_token(tampered, secret=secret, now=10_100)

    with pytest.raises(SessionTokenError, match="expired"):
        verify_session_token(token, secret=secret, now=10_300)

    revocations.revoke(payload.jti, payload.expires_at, now=10_100)
    with pytest.raises(SessionTokenError, match="revoked"):
        verify_session_token(
            token,
            secret=secret,
            revocation_store=revocations,
            now=10_150,
        )


def test_login_rate_limiter_enforces_limit_and_resets() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    assert limiter.check("127.0.0.1", "owner", now=100.0).allowed
    limiter.record_failure("127.0.0.1", "owner", now=101.0)
    limiter.record_failure("127.0.0.1", "owner", now=102.0)
    limiter.record_failure("127.0.0.1", "owner", now=103.0)

    blocked = limiter.check("10.0.0.99", "owner", now=104.0)
    assert not blocked.allowed
    assert blocked.retry_after_seconds >= 1

    # Window expiry restores access
    assert limiter.check("10.0.0.99", "owner", now=170.0).allowed

    limiter.record_failure("127.0.0.1", "owner", now=171.0)
    limiter.reset_success("127.0.0.1", "owner")
    assert limiter.check("127.0.0.1", "owner", now=172.0).allowed


def test_log_and_mapping_redaction_strips_all_secrets() -> None:
    scrubbed = redact_mapping(
        {
            "route": "/api/v1/auth/login",
            "password": "MyPlainPassword!",
            "authorization": "Bearer supersecrettoken12345",
            "x-csrf-token": "csrf-val-xyz",
            "session_secret": "ultra-secret",
            "nested": {"cookie": "media_os_session=abc.def; media_os_csrf=xyz"},
        }
    )
    serialized = json.dumps(scrubbed)
    assert "MyPlainPassword!" not in serialized
    assert "supersecrettoken12345" not in serialized
    assert "csrf-val-xyz" not in serialized
    assert "ultra-secret" not in serialized
    assert "abc.def" not in serialized
    assert scrubbed["route"] == "/api/v1/auth/login"

    raw_line = "Failed token=abc123 Bearer mytoken99999 $argon2id$v=19$m=65536$salt$hash"
    redacted_line = redact_text(raw_line)
    assert "mytoken99999" not in redacted_line
    assert "salt$hash" not in redacted_line

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Attempt Bearer secretvalue12345",
        args=(),
        exc_info=None,
    )
    record.password = "LeakCheck999"
    RedactingFilter().filter(record)
    formatted = JsonFormatter("media-os-api").format(record)
    assert "secretvalue12345" not in formatted
    assert "LeakCheck999" not in formatted
