"""HMAC-SHA256 signed session tokens with expiry, token version, CSRF secret, and revocation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass


class SessionTokenError(ValueError):
    """Raised when a session cookie token is malformed, expired, or revoked."""


@dataclass(frozen=True)
class SessionPayload:
    """Verified stateless session claim bundle."""

    subject: str
    issued_at: int
    expires_at: int
    jti: str
    csrf_token: str
    token_version: int = 1


class SessionRevocationStore:
    """Bounded in-memory revocation registry keyed by token `jti` (unit/offline fallback)."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._max_entries = max_entries
        self._revoked: dict[str, int] = {}
        self._lock = threading.Lock()

    def _prune_expired(self, now: int) -> None:
        expired = [jti for jti, exp in self._revoked.items() if exp <= now]
        for jti in expired:
            del self._revoked[jti]

    def revoke(self, jti: str, expires_at: int, *, now: int | None = None) -> None:
        current = int(time.time()) if now is None else now
        with self._lock:
            self._prune_expired(current)
            if len(self._revoked) >= self._max_entries:
                oldest_jti = next(iter(self._revoked))
                del self._revoked[oldest_jti]
            self._revoked[jti] = expires_at

    def is_revoked(self, jti: str, *, now: int | None = None) -> bool:
        current = int(time.time()) if now is None else now
        with self._lock:
            self._prune_expired(current)
            return jti in self._revoked


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))


def _sign(payload_b64: str, secret: str) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(mac)


def issue_session_token(
    *,
    subject: str,
    secret: str,
    ttl_seconds: int,
    token_version: int = 1,
    now: int | None = None,
) -> tuple[str, SessionPayload]:
    """Mint a signed session token paired with a fresh bound CSRF token."""
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + ttl_seconds
    jti = secrets.token_urlsafe(16)
    csrf_token = secrets.token_urlsafe(32)

    claims = {
        "sub": subject,
        "iat": issued_at,
        "exp": expires_at,
        "jti": jti,
        "csrf": csrf_token,
        "ver": token_version,
    }
    payload_bytes = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig_b64 = _sign(payload_b64, secret)
    token = f"{payload_b64}.{sig_b64}"
    payload = SessionPayload(
        subject=subject,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
        csrf_token=csrf_token,
        token_version=token_version,
    )
    return token, payload


def verify_session_token(
    token: str,
    *,
    secret: str,
    revocation_store: SessionRevocationStore | None = None,
    now: int | None = None,
) -> SessionPayload:
    """Validate signature, structure, expiration, and optional in-memory revocation."""
    if not token or "." not in token:
        raise SessionTokenError("Malformed session token.")

    parts = token.split(".")
    if len(parts) != 2:
        raise SessionTokenError("Malformed session token segment count.")
    payload_b64, provided_sig = parts

    expected_sig = _sign(payload_b64, secret)
    if not hmac.compare_digest(provided_sig.encode("ascii"), expected_sig.encode("ascii")):
        raise SessionTokenError("Invalid session signature.")

    try:
        decoded = _b64url_decode(payload_b64)
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SessionTokenError("Unparseable session payload.") from exc

    if not isinstance(claims, dict):
        raise SessionTokenError("Invalid session claim structure.")

    subject = claims.get("sub")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    jti = claims.get("jti")
    csrf_token = claims.get("csrf")
    token_version = claims.get("ver", 1)

    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or not isinstance(jti, str)
        or not jti
        or not isinstance(csrf_token, str)
        or not csrf_token
        or not isinstance(token_version, int)
        or token_version < 1
    ):
        raise SessionTokenError("Missing or invalid required session claims.")

    current = int(time.time()) if now is None else now
    if issued_at > current + 30:
        raise SessionTokenError("Session token issued in the future.")
    if current >= expires_at:
        raise SessionTokenError("Session token has expired.")

    if revocation_store is not None and revocation_store.is_revoked(jti, now=current):
        raise SessionTokenError("Session token has been revoked.")

    return SessionPayload(
        subject=subject,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
        csrf_token=csrf_token,
        token_version=token_version,
    )
