"""Integration-suite safety net: never run against runtime PostgreSQL or Redis."""

from __future__ import annotations

import os

import pytest
from tests.db_safety import (
    UnsafeTestTargetError,
    assert_disposable_test_database_url,
    assert_disposable_test_redis_url,
)


def pytest_configure(config: pytest.Config) -> None:
    """Validate test targets before any test (or fixture) can open a connection."""
    db_url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    try:
        if db_url:
            assert_disposable_test_database_url(
                db_url, runtime_database_url=os.environ.get("DATABASE_URL")
            )
        if redis_url:
            assert_disposable_test_redis_url(redis_url)
    except UnsafeTestTargetError as exc:
        pytest.exit(f"[TEST-TARGET-GUARD] {exc}", returncode=4)
