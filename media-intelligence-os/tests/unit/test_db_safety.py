"""Unit tests for the integration-test target guards."""

from __future__ import annotations

import pytest
from tests.db_safety import (
    UnsafeTestTargetError,
    assert_disposable_test_database_url,
    assert_disposable_test_redis_url,
)

pytestmark = pytest.mark.unit

DISPOSABLE = "postgresql+psycopg://u:p@127.0.0.1:55432/media_os_it_test"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@media-os-postgres:5432/media_os_test",  # runtime host
        "postgresql+psycopg://u:p@127.0.0.1:5432/media_os",  # runtime name
        "postgresql+psycopg://u:p@127.0.0.1:5432/postgres",  # maintenance DB
        "postgresql+psycopg://u:p@127.0.0.1:5432/media_os_dev",  # not marked disposable
        "postgresql+psycopg://u:p@127.0.0.1:5432/",  # no database
    ],
)
def test_database_guard_refuses_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeTestTargetError):
        assert_disposable_test_database_url(url, runtime_database_url=None)


def test_database_guard_accepts_disposable_and_ci_targets() -> None:
    assert_disposable_test_database_url(DISPOSABLE, runtime_database_url=None)
    ci = "postgresql+psycopg://media_os_ci:x@127.0.0.1:5432/media_os_ci_test"
    assert_disposable_test_database_url(ci, runtime_database_url=ci)


def test_database_guard_refuses_runtime_database_url_in_environment() -> None:
    runtime = "postgresql+psycopg://u:p@media-os-postgres:5432/media_os"
    with pytest.raises(UnsafeTestTargetError):
        assert_disposable_test_database_url(DISPOSABLE, runtime_database_url=runtime)


def test_database_guard_never_echoes_credentials() -> None:
    url = "postgresql+psycopg://user:super-secret-value@127.0.0.1:5432/media_os"
    with pytest.raises(UnsafeTestTargetError) as excinfo:
        assert_disposable_test_database_url(url, runtime_database_url=None)
    assert "super-secret-value" not in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "redis://media-os-redis:6379/15",
        "redis://127.0.0.1:6379/0",
        "redis://127.0.0.1:6379",
        "redis://127.0.0.1:6379/abc",
    ],
)
def test_redis_guard_refuses_unsafe_targets(url: str) -> None:
    with pytest.raises(UnsafeTestTargetError):
        assert_disposable_test_redis_url(url)


def test_redis_guard_accepts_nonzero_database() -> None:
    assert_disposable_test_redis_url("redis://127.0.0.1:56379/15")
