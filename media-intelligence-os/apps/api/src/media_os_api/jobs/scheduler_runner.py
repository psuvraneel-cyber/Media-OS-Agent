"""Dedicated lightweight runner for the persistent scheduler and outbox dispatcher."""

from __future__ import annotations

import contextlib
import logging
import pathlib
import signal
import time
from types import FrameType

from media_os_api.config import load_settings
from media_os_api.db.session import create_db_engine_from_settings, create_session_factory
from media_os_api.jobs.celery_app import build_celery_app
from media_os_api.jobs.outbox import CeleryTaskPublisher, dispatch_outbox_batch
from media_os_api.jobs.scheduler import run_scheduler_tick
from media_os_api.observability.logging import configure_logging

SCHEDULER_HEARTBEAT_PATH = pathlib.Path("/tmp/media_os_scheduler.heartbeat")  # noqa: S108

logger = logging.getLogger("media_os_api.scheduler_runner")


def _write_heartbeat(path: pathlib.Path = SCHEDULER_HEARTBEAT_PATH) -> None:
    with contextlib.suppress(OSError):
        path.write_text(str(int(time.time())), encoding="utf-8")


def main() -> int:
    settings = load_settings()
    configure_logging(level=settings.log_level, service_name="media-os-scheduler")
    engine = create_db_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    celery_instance = build_celery_app(settings)
    publisher = CeleryTaskPublisher(celery_instance)

    stop_requested = False

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        nonlocal stop_requested
        logger.info("scheduler_shutdown_signal", extra={"signal": signum})
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "scheduler_started",
        extra={
            "display_timezone": settings.app_display_timezone,
            "outbox_poll_seconds": settings.outbox_poll_interval_seconds,
            "scheduler_poll_seconds": settings.scheduler_poll_interval_seconds,
        },
    )

    last_schedule_check = 0.0
    try:
        while not stop_requested:
            _write_heartbeat()
            now_mono = time.monotonic()

            if now_mono - last_schedule_check >= settings.scheduler_poll_interval_seconds:
                try:
                    tick = run_scheduler_tick(session_factory)
                    if tick.schedules_triggered or tick.stale_leases_recovered:
                        logger.info(
                            "scheduler_tick",
                            extra={
                                "schedules_triggered": tick.schedules_triggered,
                                "jobs_created": tick.jobs_created,
                                "jobs_deduped": tick.jobs_deduped,
                                "stale_leases_recovered": tick.stale_leases_recovered,
                            },
                        )
                except Exception as exc:
                    logger.error(
                        "scheduler_tick_error",
                        extra={"error_category": type(exc).__name__},
                    )
                last_schedule_check = now_mono

            try:
                stats = dispatch_outbox_batch(session_factory, publisher=publisher)
                if stats.claimed:
                    logger.info(
                        "outbox_dispatch_batch",
                        extra={
                            "claimed": stats.claimed,
                            "published": stats.published,
                            "retried": stats.retried,
                            "dead_lettered": stats.dead_lettered,
                        },
                    )
            except Exception as exc:
                logger.error(
                    "outbox_dispatch_error",
                    extra={"error_category": type(exc).__name__},
                )

            _write_heartbeat()
            time.sleep(settings.outbox_poll_interval_seconds)
    finally:
        with contextlib.suppress(OSError):
            SCHEDULER_HEARTBEAT_PATH.unlink(missing_ok=True)
        engine.dispose()
        logger.info("scheduler_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
