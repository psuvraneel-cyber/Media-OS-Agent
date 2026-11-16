"""Phase 4 harmless internal Celery tasks and task handler registry."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from media_os_api.analytics.account import ACCOUNT_TASK, handle_sync_account_metrics
from media_os_api.analytics.sync import SYNC_METRICS_TASK, handle_sync_post_metrics
from media_os_api.carousel.render_job import RENDER_TASK, handle_render_carousel
from media_os_api.config import Settings, load_settings
from media_os_api.db.models import JobRun
from media_os_api.db.retention import run_retention_evaluation
from media_os_api.db.session import create_db_engine_from_settings, create_session_factory
from media_os_api.jobs.celery_app import celery_app
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.jobs.ledger import TaskHandler, execute_durable_job
from media_os_api.jobs.purge import PURGE_TASK, handle_purge_job_history
from media_os_api.jobs.routing import (
    CREATIVE_CELERY_TASK,
    CREATIVE_LEASE_SECONDS,
    CREATIVE_SOFT_TIME_LIMIT,
    CREATIVE_TIME_LIMIT,
    RESEARCH_CELERY_TASK,
    RESEARCH_LEASE_SECONDS,
    RESEARCH_SOFT_TIME_LIMIT,
    RESEARCH_TIME_LIMIT,
)
from media_os_api.notify.commands import COMMANDS_TASK, handle_telegram_commands
from media_os_api.notify.service import (
    DIGEST_TASK,
    NOTIFY_TASK,
    WATCHDOG_TASK,
    handle_daily_digest,
    handle_send_telegram,
    handle_watchdog,
)
from media_os_api.publish.service import (
    SCHEDULE_TASK,
    SYNC_TASK,
    handle_schedule_carousel,
    handle_sync_status,
)
from media_os_api.research.jobs import research_fetch_registry
from media_os_api.research.tasks import handle_research_ingestion

logger = logging.getLogger("media_os_api.worker")

_cached_session_factory: sessionmaker[Session] | None = None
_cached_settings: Settings | None = None


def _get_worker_context() -> tuple[Settings, sessionmaker[Session]]:
    global _cached_settings, _cached_session_factory
    if _cached_settings is None or _cached_session_factory is None:
        settings = load_settings()
        engine = create_db_engine_from_settings(settings)
        _cached_settings = settings
        _cached_session_factory = create_session_factory(engine)
    return _cached_settings, _cached_session_factory


def handle_internal_ping(_session: Session, job: JobRun) -> dict[str, Any]:
    """Harmless Phase 4 verification task confirming end-to-end worker durability."""
    payload = dict(job.payload or {})
    return {
        "ok": True,
        "task_name": job.task_name,
        "job_run_id": str(job.id),
        "brand_profile_id": str(job.brand_profile_id) if job.brand_profile_id else None,
        "attempt_count": job.attempt_count,
        "echo": payload.get("echo", "pong"),
        "completed_at_utc": datetime.now(UTC).isoformat(),
    }


def handle_retention_dry_run(session: Session, job: JobRun) -> dict[str, Any]:
    """Run non-destructive Phase 4 retention policy evaluation (dry-run only by policy)."""
    payload = dict(job.payload or {})
    if payload.get("dry_run") is False:
        raise PermanentTaskError(
            "Automatic destructive retention cleanup is disabled until policy validation "
            "completes.",
            code="RETENTION_DESTRUCTIVE_DISABLED",
        )
    settings = _cached_settings
    media_dir = Path(settings.media_storage_dir) if settings else None
    warn_pct = settings.media_storage_warn_percent if settings else 80
    report = run_retention_evaluation(
        session,
        dry_run=True,
        actor_id="worker:retention_dry_run",
        media_dir=media_dir,
        warn_percent=warn_pct,
    )
    return {
        "dry_run": report.dry_run,
        "evaluated_at_utc": report.evaluated_at_utc,
        "expired_auth_sessions": report.expired_auth_sessions,
        "expired_source_items": report.expired_source_items,
        "expired_metrics_snapshots": report.expired_metrics_snapshots,
        "stale_youtube_authorizations": report.stale_youtube_authorizations,
        "expired_temp_assets": report.expired_temp_assets,
        "protected_referenced_assets": report.protected_referenced_assets,
        "expired_audit_events": report.expired_audit_events,
    }


PHASE4_TASK_REGISTRY: dict[str, TaskHandler] = {
    "media_os.internal.ping": handle_internal_ping,
    "media_os.internal.retention_dry_run": handle_retention_dry_run,
    "media_os.research.ingest": handle_research_ingestion,
    RENDER_TASK: handle_render_carousel,  # executes only in the creative container
    SCHEDULE_TASK: handle_schedule_carousel,  # N3: worker only (GitHub + Postiz credentials)
    SYNC_TASK: handle_sync_status,
    SYNC_METRICS_TASK: handle_sync_post_metrics,  # worker only (Postiz credentials)
    ACCOUNT_TASK: handle_sync_account_metrics,  # worker only (Postiz credentials)
    PURGE_TASK: handle_purge_job_history,
    # Owner alerts (worker only: holds TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID and the Postiz key).
    NOTIFY_TASK: handle_send_telegram,
    WATCHDOG_TASK: handle_watchdog,
    DIGEST_TASK: handle_daily_digest,
    COMMANDS_TASK: handle_telegram_commands,  # /status, /queue, /help from the owner's chat
}


def _worker_session_factory(_session: Session) -> sessionmaker[Session]:
    _settings, factory = _get_worker_context()
    return factory


# Scheduled research fetches (one per registered fetcher); payloads carry no content.
PHASE4_TASK_REGISTRY.update(research_fetch_registry(_worker_session_factory))


@celery_app.task(name="media_os.execute_job_run", acks_late=True)  # type: ignore[untyped-decorator]
def execute_job_run(job_run_id: str) -> dict[str, Any]:
    """Celery task wrapper executing a durable JobRun with lease protection."""
    settings, session_factory = _get_worker_context()
    parsed_id = uuid.UUID(job_run_id)
    logger.info(
        "worker_executing_job",
        extra={"job_run_id": job_run_id, "concurrency": settings.worker_concurrency},
    )
    return execute_durable_job(
        session_factory,
        job_run_id=parsed_id,
        registry=PHASE4_TASK_REGISTRY,
        lease_seconds=settings.job_lease_seconds,
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name=RESEARCH_CELERY_TASK,
    acks_late=True,
    soft_time_limit=RESEARCH_SOFT_TIME_LIMIT,
    time_limit=RESEARCH_TIME_LIMIT,
)
def execute_research_job_run(job_run_id: str) -> dict[str, Any]:
    """Research fetches: same durable execution, longer time limits and lease (paced upstreams)."""
    settings, session_factory = _get_worker_context()
    logger.info(
        "worker_executing_research_job",
        extra={"job_run_id": job_run_id, "concurrency": settings.worker_concurrency},
    )
    return execute_durable_job(
        session_factory,
        job_run_id=uuid.UUID(job_run_id),
        registry=PHASE4_TASK_REGISTRY,
        lease_seconds=max(settings.job_lease_seconds, RESEARCH_LEASE_SECONDS),
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name=CREATIVE_CELERY_TASK,
    acks_late=True,
    soft_time_limit=CREATIVE_SOFT_TIME_LIMIT,
    time_limit=CREATIVE_TIME_LIMIT,
)
def execute_creative_job_run(job_run_id: str) -> dict[str, Any]:
    """Creative jobs (slide rendering): consumed only by the creative container's worker."""
    settings, session_factory = _get_worker_context()
    logger.info("worker_executing_creative_job", extra={"job_run_id": job_run_id})
    return execute_durable_job(
        session_factory,
        job_run_id=uuid.UUID(job_run_id),
        registry=PHASE4_TASK_REGISTRY,
        lease_seconds=max(settings.job_lease_seconds, CREATIVE_LEASE_SECONDS),
    )
