"""Fail-closed configuration management for the Media Intelligence OS FastAPI and worker runtime."""

from __future__ import annotations

import base64
import binascii
import os
from typing import Annotated, Any, Literal
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "VERIFY_IN_PHASE_")
WEAK_SECRETS = frozenset(
    {
        "changeme",
        "secret",
        "development",
        "dev-secret",
        "default",
        "password",
        "media-intelligence-os",
        "12345678901234567890123456789012",
    }
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


class ConfigurationError(ValueError):
    """Raised when required application configuration is missing or unsafe."""


def _is_placeholder(value: str) -> bool:
    upper = value.strip().upper()
    return any(upper.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES)


def _coerce_empty_secret(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _validate_postgres_dsn(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned or _is_placeholder(cleaned):
        raise ValueError("DATABASE_URL must be configured (non-placeholder).")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use postgresql:// or postgresql+psycopg://.")
    if not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL must include host and database name.")
    if not parsed.username or not parsed.password or _is_placeholder(parsed.password):
        raise ValueError("DATABASE_URL must include non-placeholder username and password.")
    return cleaned


def _validate_redis_dsn(raw: str, *, field_name: str) -> str:
    cleaned = raw.strip()
    if not cleaned or _is_placeholder(cleaned):
        raise ValueError(f"{field_name} must be configured (non-placeholder).")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError(f"{field_name} must use redis:// or rediss://.")
    if not parsed.hostname:
        raise ValueError(f"{field_name} must include a Redis host.")
    if parsed.password is not None and _is_placeholder(parsed.password):
        raise ValueError(f"{field_name} must not contain a placeholder password.")
    return cleaned


class Settings(BaseSettings):
    """Typed, fail-closed application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_phase: str = "phase-4-persistence"
    service_name: str = "media-os-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_display_timezone: str = "Asia/Kolkata"

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    owner_username: str = Field(..., min_length=1, max_length=128)
    owner_password_hash: SecretStr | None = None
    owner_password_hash_b64: SecretStr | None = None
    session_secret: SecretStr

    session_ttl_seconds: int = Field(default=28800, ge=60, le=604800)
    cookie_secure: bool = True
    cookie_name: str = "media_os_session"
    csrf_cookie_name: str = "media_os_csrf"

    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:3100", "http://localhost:3100"]
    )

    login_rate_limit_max_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=300, ge=10, le=3600)
    rate_limit_fail_closed_on_redis_outage: bool = False

    # Persistence & Worker Configuration (Phase 4)
    require_persistence: bool | None = None
    postgres_host: str = "media-os-postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "media_os"
    postgres_user: str = "media_os_user"
    postgres_password: SecretStr | None = None
    database_url: SecretStr | None = None

    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)

    redis_host: str = "media-os-redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr | None = None
    redis_url: SecretStr | None = None
    redis_socket_timeout_seconds: float = Field(default=2.0, ge=0.2, le=15.0)

    celery_broker_url: SecretStr | None = None
    celery_result_backend: SecretStr | None = None
    worker_concurrency: int = Field(default=1, ge=1, le=4)
    job_lease_seconds: int = Field(default=120, ge=10, le=3600)
    outbox_poll_interval_seconds: float = Field(default=1.0, ge=0.2, le=60.0)
    scheduler_poll_interval_seconds: float = Field(default=5.0, ge=1.0, le=300.0)

    # Data Retention Policy Defaults (Phase 4)
    retention_source_default_days: int = Field(default=365, ge=1, le=3650)
    retention_youtube_non_auth_days: int = Field(default=30, ge=1, le=30)
    retention_youtube_verify_interval_days: int = Field(default=30, ge=1, le=30)
    retention_app_log_days: int = Field(default=30, ge=1, le=365)
    retention_audit_event_days: int = Field(default=365, ge=30, le=3650)
    retention_failed_temp_media_days: int = Field(default=7, ge=1, le=90)
    media_storage_dir: str = "/tmp/media_os_artifacts"  # noqa: S108
    media_storage_warn_percent: int = Field(default=80, ge=10, le=99)

    postiz_base_url: str = "http://127.0.0.1:4007"
    postiz_health_timeout_seconds: float = Field(default=2.0, ge=0.2, le=10.0)
    source_reddit_status: str = "BLOCKED"

    @field_validator("app_display_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        cleaned = value.strip()
        try:
            ZoneInfo(cleaned)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {cleaned}") from exc
        return cleaned

    @field_validator("owner_username")
    @classmethod
    def _validate_owner_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or _is_placeholder(cleaned):
            raise ValueError("OWNER_USERNAME must be a non-placeholder owner identifier.")
        return cleaned

    @field_validator(
        "owner_password_hash",
        "owner_password_hash_b64",
        "postgres_password",
        "database_url",
        "redis_password",
        "redis_url",
        "celery_broker_url",
        "celery_result_backend",
        mode="before",
    )
    @classmethod
    def _coerce_empty_secrets(cls, value: Any) -> Any:
        return _coerce_empty_secret(value)

    @model_validator(mode="after")
    def _resolve_and_validate_password_hash(self) -> Settings:
        raw: str | None = None
        if self.owner_password_hash is not None:
            raw = self.owner_password_hash.get_secret_value().strip()
        elif self.owner_password_hash_b64 is not None:
            encoded = self.owner_password_hash_b64.get_secret_value().strip()
            if not encoded or _is_placeholder(encoded):
                raise ValueError("OWNER_PASSWORD_HASH_B64 must be configured (non-placeholder).")
            try:
                raw = (
                    base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8").strip()
                )
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError("OWNER_PASSWORD_HASH_B64 must be valid base64 text.") from exc
            self.owner_password_hash = SecretStr(raw)

        if not raw or _is_placeholder(raw):
            raise ValueError("OWNER_PASSWORD_HASH or OWNER_PASSWORD_HASH_B64 must be configured.")
        if not raw.startswith("$argon2id$"):
            raise ValueError("Resolved owner password hash must use Argon2id ($argon2id$...).")
        try:
            extract_parameters(raw)
        except InvalidHashError as exc:
            raise ValueError("Resolved owner password hash is not valid Argon2id.") from exc
        if self.owner_password_hash is None:
            self.owner_password_hash = SecretStr(raw)
        return self

    def resolved_owner_password_hash(self) -> str:
        """Return validated `$argon2id$...` password hash string."""
        if self.owner_password_hash is None:
            raise ConfigurationError("Owner password hash is not configured.")
        return self.owner_password_hash.get_secret_value()

    @field_validator("session_secret")
    @classmethod
    def _validate_session_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if not raw or _is_placeholder(raw):
            raise ValueError("SESSION_SECRET must be configured (non-placeholder).")
        if len(raw) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters long.")
        if raw.lower() in WEAK_SECRETS or len(set(raw)) < 8:
            raise ValueError("SESSION_SECRET must have high entropy and avoid weak defaults.")
        return SecretStr(raw)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            origins = [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        elif isinstance(value, list):
            origins = [str(item).strip().rstrip("/") for item in value if str(item).strip()]
        else:
            raise ValueError("CORS_ALLOWED_ORIGINS must be a comma-separated string or list.")
        if not origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must include at least one allowed origin.")
        for origin in origins:
            if origin == "*":
                raise ValueError("Wildcard CORS origin '*' is forbidden with credentialed cookies.")
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Invalid CORS origin format: {origin}")
        return origins

    @field_validator("postiz_base_url")
    @classmethod
    def _validate_postiz_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("POSTIZ_BASE_URL must be an http:// or https:// URL.")
        return cleaned

    @model_validator(mode="after")
    def _resolve_and_validate_persistence(self) -> Settings:
        must_persist = (
            self.require_persistence
            if self.require_persistence is not None
            else (self.app_env == "production")
        )

        # Resolve PostgreSQL DSN
        if self.database_url is not None:
            pg_dsn = _validate_postgres_dsn(self.database_url.get_secret_value())
            self.database_url = SecretStr(pg_dsn)
        elif self.postgres_password is not None:
            pw = self.postgres_password.get_secret_value().strip()
            if not pw or _is_placeholder(pw):
                raise ValueError("POSTGRES_PASSWORD must be configured (non-placeholder).")
            pg_dsn = (
                f"postgresql+psycopg://{quote_plus(self.postgres_user)}:{quote_plus(pw)}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
            self.database_url = SecretStr(pg_dsn)
        elif must_persist:
            raise ValueError(
                "DATABASE_URL (or POSTGRES_PASSWORD) must be configured when persistence is "
                "required."
            )

        # Resolve Redis DSN and Celery URLs
        if self.redis_url is not None:
            redis_dsn = _validate_redis_dsn(
                self.redis_url.get_secret_value(),
                field_name="REDIS_URL",
            )
            self.redis_url = SecretStr(redis_dsn)
        elif self.redis_password is not None:
            rpw = self.redis_password.get_secret_value().strip()
            if not rpw or _is_placeholder(rpw):
                raise ValueError("REDIS_PASSWORD must be configured (non-placeholder).")
            redis_dsn = f"redis://:{quote_plus(rpw)}@{self.redis_host}:{self.redis_port}/0"
            self.redis_url = SecretStr(redis_dsn)
        elif must_persist:
            raise ValueError(
                "REDIS_URL (or REDIS_PASSWORD) must be configured when persistence is required."
            )

        if self.celery_broker_url is not None:
            broker_dsn = _validate_redis_dsn(
                self.celery_broker_url.get_secret_value(),
                field_name="CELERY_BROKER_URL",
            )
            self.celery_broker_url = SecretStr(broker_dsn)
        elif self.redis_url is not None:
            self.celery_broker_url = SecretStr(self.redis_url.get_secret_value())

        if self.celery_result_backend is not None:
            backend_dsn = _validate_redis_dsn(
                self.celery_result_backend.get_secret_value(),
                field_name="CELERY_RESULT_BACKEND",
            )
            self.celery_result_backend = SecretStr(backend_dsn)
        elif self.redis_url is not None:
            parsed = urlparse(self.redis_url.get_secret_value())
            auth_prefix = f":{parsed.password}@" if parsed.password else ""
            port = parsed.port or 6379
            self.celery_result_backend = SecretStr(
                f"{parsed.scheme}://{auth_prefix}{parsed.hostname}:{port}/1"
            )

        return self

    @model_validator(mode="after")
    def _validate_cookie_security_boundary(self) -> Settings:
        if not self.cookie_secure:
            for origin in self.cors_allowed_origins:
                host = (urlparse(origin).hostname or "").lower()
                if host not in LOOPBACK_HOSTS:
                    raise ValueError(
                        "COOKIE_SECURE=false is only permitted when CORS_ALLOWED_ORIGINS "
                        "targets loopback hosts (127.0.0.1 / localhost)."
                    )
        return self

    def resolved_database_url(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url is not None else None

    def resolved_redis_url(self) -> str | None:
        return self.redis_url.get_secret_value() if self.redis_url is not None else None

    def resolved_celery_broker_url(self) -> str | None:
        if self.celery_broker_url is not None:
            return self.celery_broker_url.get_secret_value()
        return self.resolved_redis_url()

    def resolved_celery_result_backend(self) -> str | None:
        if self.celery_result_backend is not None:
            return self.celery_result_backend.get_secret_value()
        return self.resolved_redis_url()

    @property
    def persistence_enabled(self) -> bool:
        return self.database_url is not None


def load_settings(env_overrides: dict[str, str] | None = None) -> Settings:
    """Load and validate application settings, raising ConfigurationError safely."""
    try:
        if env_overrides is not None:
            return Settings(**env_overrides)  # type: ignore[arg-type]
        return Settings(
            **{k: v for k, v in os.environ.items() if v != ""},  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        # Never include raw field values in error messages because secrets may be malformed.
        missing_or_invalid = sorted(
            {f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()}
        )
        summary = "; ".join(missing_or_invalid)
        raise ConfigurationError(f"Invalid application configuration: {summary}") from exc
