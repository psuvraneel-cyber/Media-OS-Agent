"""Durable job handlers for scheduled research fetches (`media_os.research.fetch.<source>`).

Payloads carry no content — only `{"source": "<CODE>", "trigger": "schedule|manual"}`. The handler
builds the registered fetcher and runs it through the shared runner, which enforces the three-switch
live-fetch gate before any HTTP request. Source-specific state (checkpoints, health) lives in the
registry tables, so a retried or redelivered job is safe.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db.models import JobRun
from media_os_api.db.session import create_session_factory
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.jobs.routing import RESEARCH_FETCH_PREFIX
from media_os_api.research.fetchers import FETCHERS, SourceFetcher
from media_os_api.research.http import RateLimiter, SafeHttpClient, build_redis_rate_limiter
from media_os_api.research.runner import HttpFactory, run_source_fetch

RESEARCH_TASK_TYPE = "RESEARCH_FETCH"
FETCH_TASK_PREFIX = RESEARCH_FETCH_PREFIX
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"  # noqa: S105 - environment variable *name*, not a secret


def fetch_task_name(source_code: str) -> str:
    return f"{FETCH_TASK_PREFIX}{source_code.lower()}"


def is_research_fetch_task(task_name: str) -> bool:
    return task_name.startswith(FETCH_TASK_PREFIX)


def production_http_factory(
    rate_limiter: RateLimiter | None = None, environ: Mapping[str, str] | None = None
) -> HttpFactory:
    """HTTP factory for workers: Redis limiter (fail-closed) and optional GitHub token from env."""
    env = os.environ if environ is None else environ
    limiter = rate_limiter

    def factory(fetcher: SourceFetcher, config: Mapping[str, Any]) -> SafeHttpClient:
        nonlocal limiter
        if limiter is None:
            from media_os_api.config import load_settings

            settings = load_settings()
            if settings.redis_url is None:
                raise PermanentTaskError(
                    "Redis is not configured; research rate limiting unavailable.",
                    code="RATE_LIMITER_NOT_CONFIGURED",
                )
            limiter = build_redis_rate_limiter(settings.redis_url.get_secret_value())
        token = env.get(GITHUB_TOKEN_ENV) if fetcher.host_policy.auth_hosts else None
        return SafeHttpClient(
            source_code=fetcher.source_code,
            policy=fetcher.host_policy,
            rate_limiter=limiter,
            min_interval_ms=fetcher.min_interval_ms(config),
            bearer_token=token or None,
        )

    return factory


def make_fetch_handler(
    *,
    http_factory: HttpFactory | None = None,
    session_factory_provider: Callable[[Session], sessionmaker[Session]] | None = None,
    env_enabled: bool | None = None,
) -> Callable[[Session, JobRun], dict[str, Any]]:
    """Build the JobRun handler. Arguments exist for tests; production uses the defaults."""

    def handle_research_fetch(session: Session, job: JobRun) -> dict[str, Any]:
        payload = dict(job.payload or {})
        source = str(payload.get("source") or "").strip().upper()
        fetcher_cls = FETCHERS.get(source)
        if fetcher_cls is None:
            raise PermanentTaskError(
                f"No fetcher registered for research source '{source}'.",
                code="UNKNOWN_RESEARCH_SOURCE",
            )
        if job.task_name != fetch_task_name(source):
            raise PermanentTaskError(
                "Job task name does not match its payload source.", code="TASK_SOURCE_MISMATCH"
            )
        factory = (
            session_factory_provider(session)
            if session_factory_provider is not None
            else create_session_factory(session.get_bind())  # type: ignore[arg-type]
        )
        outcome = run_source_fetch(
            factory,
            fetcher_cls(),
            http_factory=http_factory or production_http_factory(),
            env_enabled=env_enabled,
        )
        return {"trigger": str(payload.get("trigger") or "manual"), **outcome.as_dict()}

    return handle_research_fetch


def research_fetch_registry(
    session_factory_provider: Callable[[Session], sessionmaker[Session]] | None = None,
) -> dict[str, Callable[[Session, JobRun], dict[str, Any]]]:
    """Task-name → handler for every source that has a complete fetcher."""
    handler = make_fetch_handler(session_factory_provider=session_factory_provider)
    return {fetch_task_name(code): handler for code in FETCHERS}
