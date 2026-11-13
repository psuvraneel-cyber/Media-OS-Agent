"""Guards that keep destructive integration tests away from the runtime application database.

Several integration tests run `DROP SCHEMA public CASCADE`, Alembic downgrades and fixture inserts.
On 2026-11-13 the suite was pointed at the runtime `media_os` database and reset it. These guards
make that impossible by refusing any test target that is not an explicitly disposable database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Hostnames of the long-lived runtime stack (`infra/docker/compose.app.yml`).
RUNTIME_DB_HOSTS: frozenset[str] = frozenset({"media-os-postgres"})
RUNTIME_REDIS_HOSTS: frozenset[str] = frozenset({"media-os-redis"})
RUNTIME_DB_NAMES: frozenset[str] = frozenset({"media_os", "postgres", "template0", "template1"})

# Disposable databases must say so in their name, e.g. `media_os_it_test`, `media_os_ci_test`.
_DISPOSABLE_DB_NAME = re.compile(r"^[a-z0-9_]+_test$")


class UnsafeTestTargetError(RuntimeError):
    """Raised when a test URL could point at runtime (non-disposable) data."""


@dataclass(frozen=True)
class _Target:
    host: str
    port: int | None
    path: str


def _parse(url: str) -> _Target:
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.hostname:
        raise UnsafeTestTargetError("Test URL must include a scheme and host.")
    return _Target(
        host=parts.hostname.lower(), port=parts.port, path=parts.path.lstrip("/").lower()
    )


def assert_disposable_test_database_url(url: str, *, runtime_database_url: str | None) -> None:
    """Refuse any PostgreSQL URL that is not an explicitly disposable test database.

    Error messages name the offending host/database only; credentials are never echoed.
    """
    target = _parse(url)
    if not target.path:
        raise UnsafeTestTargetError("Test database URL must name a database.")
    if target.host in RUNTIME_DB_HOSTS:
        raise UnsafeTestTargetError(
            f"Refusing test database on runtime host '{target.host}'. "
            "Use the disposable stack: python scripts/run_integration_tests.py"
        )
    if target.path in RUNTIME_DB_NAMES:
        raise UnsafeTestTargetError(f"Refusing reserved/runtime database name '{target.path}'.")
    if not _DISPOSABLE_DB_NAME.match(target.path):
        raise UnsafeTestTargetError(
            f"Test database name '{target.path}' must end with '_test' to mark it disposable."
        )
    # Alembic and some tests read DATABASE_URL directly. When it is set during a test run it must
    # be the same disposable database, never the runtime one.
    if runtime_database_url and runtime_database_url.strip():
        other = _parse(runtime_database_url)
        if other.host in RUNTIME_DB_HOSTS or not _DISPOSABLE_DB_NAME.match(other.path):
            raise UnsafeTestTargetError(
                "DATABASE_URL is set to a non-disposable database during the integration run; "
                f"refusing (host='{other.host}', database='{other.path}')."
            )


def assert_disposable_test_redis_url(url: str) -> None:
    """Refuse Redis URLs on the runtime host or the runtime logical database 0."""
    target = _parse(url)
    if target.host in RUNTIME_REDIS_HOSTS:
        raise UnsafeTestTargetError(f"Refusing test Redis on runtime host '{target.host}'.")
    db_index = target.path or "0"
    if not db_index.isdigit() or int(db_index) == 0:
        raise UnsafeTestTargetError(
            "Test Redis URL must select a non-zero logical database (e.g. /15)."
        )
