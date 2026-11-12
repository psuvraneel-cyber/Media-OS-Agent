"""Argon2id password hashing and constant-time credential verification."""

from __future__ import annotations

import hmac
import secrets
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()
_DUMMY_LOCK = threading.Lock()
_DUMMY_HASH: str | None = None


def _get_dummy_hash() -> str:
    """Lazily compute an inert runtime Argon2id hash to equalize invalid-username timing."""
    global _DUMMY_HASH
    with _DUMMY_LOCK:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(24))
        return _DUMMY_HASH


def hash_password(plain_password: str) -> str:
    """Hash a plaintext owner password using Argon2id."""
    if not plain_password or len(plain_password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    return _HASHER.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id hash without raising on mismatch."""
    if not plain_password or not password_hash:
        return False
    try:
        return bool(_HASHER.verify(password_hash, plain_password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_owner_credentials(
    *,
    submitted_username: str,
    submitted_password: str,
    configured_username: str,
    configured_password_hash: str,
) -> bool:
    """Verify single-owner credentials using constant-time username and Argon2id work."""
    username_ok = hmac.compare_digest(
        submitted_username.strip().lower().encode("utf-8"),
        configured_username.strip().lower().encode("utf-8"),
    )
    if not username_ok:
        verify_password(submitted_password or "invalid-password", _get_dummy_hash())
        return False
    return verify_password(submitted_password, configured_password_hash)
