"""Alembic migration environment for Media Intelligence OS."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from media_os_api.db import models  # noqa: F401
from media_os_api.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_db_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if "db_url" in x_args and x_args["db_url"]:
        return str(x_args["db_url"])
    configured = config.get_main_option("sqlalchemy.url")
    if configured and configured.strip():
        return configured.strip()
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url
    from media_os_api.config import load_settings

    settings = load_settings()
    resolved = settings.resolved_database_url()
    if not resolved:
        raise RuntimeError("DATABASE_URL must be configured to run Alembic migrations.")
    return resolved


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _resolve_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_db_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
