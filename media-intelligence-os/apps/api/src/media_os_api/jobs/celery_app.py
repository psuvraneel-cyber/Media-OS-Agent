"""Celery application configuration with JSON-only serialization and explicit queues."""

from __future__ import annotations

import contextlib
import os
import pathlib
import threading
import time
from typing import Any

from celery import Celery
from celery.signals import worker_ready, worker_shutting_down
from kombu.entity import Exchange, Queue

from media_os_api.config import Settings, load_settings

WORKER_HEARTBEAT_PATH = pathlib.Path("/tmp/media_os_worker.heartbeat")  # noqa: S108

_exchange_cls: Any = Exchange
_queue_cls: Any = Queue

DEFAULT_EXCHANGE: Any = _exchange_cls("media_os", type="direct")

CELERY_QUEUES: tuple[Any, ...] = (
    _queue_cls("control", DEFAULT_EXCHANGE, routing_key="control"),
    _queue_cls("maintenance", DEFAULT_EXCHANGE, routing_key="maintenance"),
    _queue_cls("default", DEFAULT_EXCHANGE, routing_key="default"),
    # Reserved queues for Phases 5 and 7 so queue topology requires no future redesign
    _queue_cls("research", DEFAULT_EXCHANGE, routing_key="research"),
    _queue_cls("creative", DEFAULT_EXCHANGE, routing_key="creative"),
)

_heartbeat_stop = threading.Event()
_heartbeat_thread: threading.Thread | None = None


def _write_heartbeat(path: pathlib.Path = WORKER_HEARTBEAT_PATH) -> None:
    with contextlib.suppress(OSError):
        path.write_text(str(int(time.time())), encoding="utf-8")


def _heartbeat_loop(path: pathlib.Path = WORKER_HEARTBEAT_PATH) -> None:
    while not _heartbeat_stop.is_set():
        _write_heartbeat(path)
        _heartbeat_stop.wait(10.0)


@worker_ready.connect  # type: ignore[untyped-decorator]
def _on_worker_ready(**_kwargs: Any) -> None:
    global _heartbeat_thread
    _heartbeat_stop.clear()
    _write_heartbeat()
    if _heartbeat_thread is None or not _heartbeat_thread.is_alive():
        _heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        _heartbeat_thread.start()


@worker_shutting_down.connect  # type: ignore[untyped-decorator]
def _on_worker_shutting_down(**_kwargs: Any) -> None:
    _heartbeat_stop.set()
    with contextlib.suppress(OSError):
        WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)


def build_celery_app(settings: Settings | None = None) -> Celery:
    """Create a Celery application configured strictly with JSON serialization."""
    broker_url = "memory://"
    result_backend = "cache+memory://"
    concurrency = 1
    timezone_name = "Asia/Kolkata"

    if settings is not None:
        broker_url = settings.resolved_celery_broker_url() or broker_url
        result_backend = settings.resolved_celery_result_backend() or result_backend
        concurrency = settings.worker_concurrency
        timezone_name = settings.app_display_timezone
    else:
        env_broker = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL")
        env_backend = os.environ.get("CELERY_RESULT_BACKEND") or env_broker
        if env_broker:
            try:
                loaded = load_settings()
                broker_url = loaded.resolved_celery_broker_url() or env_broker
                result_backend = loaded.resolved_celery_result_backend() or (
                    env_backend or broker_url
                )
                concurrency = loaded.worker_concurrency
                timezone_name = loaded.app_display_timezone
            except Exception:
                broker_url = env_broker
                result_backend = env_backend or env_broker

    app = Celery("media_intelligence_os", include=["media_os_api.jobs.tasks"])
    app.conf.update(
        broker_url=broker_url,
        result_backend=result_backend,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        event_serializer="json",
        enable_utc=True,
        timezone=timezone_name,
        task_default_queue="control",
        task_default_exchange="media_os",
        task_default_routing_key="control",
        task_queues=CELERY_QUEUES,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_concurrency=concurrency,
        task_soft_time_limit=90,
        task_time_limit=120,
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": 300},
    )
    return app


celery_app = build_celery_app()
