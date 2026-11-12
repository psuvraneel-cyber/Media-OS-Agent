"""Centralized synchronous PostgreSQL engine, pool, session lifecycle, and health helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.config import Settings
from media_os_api.observability.redaction import redact_text

logger = logging.getLogger("media_os_api.db")


class DatabaseUnavailableError(RuntimeError):
    """Raised when PostgreSQL cannot be reached or fails health checks."""


def create_db_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_timeout_seconds: int = 10,
    connect_timeout_seconds: int = 5,
    pool_recycle_seconds: int = 1800,
) -> Engine:
    """Create a bounded synchronous SQLAlchemy 2.x PostgreSQL engine."""
    connect_args: dict[str, object] = {}
    if database_url.startswith("postgresql"):
        connect_args = {
            "connect_timeout": connect_timeout_seconds,
            "options": "-c timezone=UTC",
        }
    return create_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        pool_recycle=pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )


def create_db_engine_from_settings(settings: Settings) -> Engine:
    """Create a bounded database engine using validated runtime Settings."""
    dsn = settings.resolved_database_url()
    if dsn is None:
        raise DatabaseUnavailableError("DATABASE_URL is not configured.")
    return create_db_engine(
        dsn,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout_seconds=settings.db_pool_timeout_seconds,
        connect_timeout_seconds=settings.db_connect_timeout_seconds,
        pool_recycle_seconds=settings.db_pool_recycle_seconds,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create consistent synchronous SQLAlchemy sessionmaker."""
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide an explicit transactional session boundary with automatic rollback on failure."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_health(engine: Engine) -> tuple[bool, str, str | None]:
    """Verify PostgreSQL connectivity and schema version without exposing connection strings."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            version_row = conn.execute(
                text(
                    "SELECT CASE WHEN to_regclass('public.alembic_version') IS NOT NULL "
                    "THEN (SELECT version_num FROM alembic_version LIMIT 1) ELSE NULL END"
                )
            ).scalar_one_or_none()
        if version_row is None:
            return False, "PostgreSQL reachable but Alembic schema migration has not run.", None
        return True, f"PostgreSQL healthy (schema={version_row}).", str(version_row)
    except SQLAlchemyError as exc:
        sanitized = redact_text(type(exc).__name__)
        logger.warning("postgres_healthcheck_failed", extra={"error_category": sanitized})
        return False, "PostgreSQL connection unavailable.", None
