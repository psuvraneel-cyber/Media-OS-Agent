"""Transactional outbox dispatcher using PostgreSQL FOR UPDATE SKIP LOCKED."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from celery import Celery
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db.models import JobRun, OutboxEvent
from media_os_api.db.session import session_scope
from media_os_api.jobs.errors import compute_backoff_seconds
from media_os_api.jobs.routing import celery_task_for_queue
from media_os_api.observability.redaction import redact_text

logger = logging.getLogger("media_os_api.outbox")


class TaskPublisher(Protocol):
    """Abstract queue publisher implemented by CeleryTaskPublisher and test fakes."""

    def publish_job(
        self,
        *,
        job_run_id: uuid.UUID,
        task_name: str,
        queue_name: str,
        attempt: int,
        request_id: str,
    ) -> str:
        """Dispatch a durable JobRun execution message and return broker task ID."""


class CeleryTaskPublisher:
    """Production Celery task publisher routing durable JobRun IDs via JSON messages."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    def publish_job(
        self,
        *,
        job_run_id: uuid.UUID,
        task_name: str,
        queue_name: str,
        attempt: int,
        request_id: str,
    ) -> str:
        celery_task_id = f"job-{job_run_id}-d{attempt}-{uuid.uuid4().hex[:8]}"
        async_result = self._app.send_task(
            celery_task_for_queue(queue_name),
            kwargs={"job_run_id": str(job_run_id)},
            queue=queue_name,
            routing_key=queue_name,
            task_id=celery_task_id,
            headers={
                "request_id": request_id,
                "logical_task_name": task_name,
                "job_run_id": str(job_run_id),
            },
        )
        return str(async_result.id)


@dataclass(frozen=True)
class ClaimedOutboxItem:
    """In-memory snapshot of an outbox event claimed for dispatch."""

    outbox_id: uuid.UUID
    job_run_id: uuid.UUID
    task_name: str
    queue_name: str
    attempt_count: int
    maximum_attempts: int
    request_id: str


@dataclass(frozen=True)
class OutboxDispatchStats:
    """Summary counts from one dispatcher batch pass."""

    claimed: int
    published: int
    retried: int
    dead_lettered: int


def claim_outbox_batch(
    session: Session,
    *,
    batch_size: int = 10,
    claim_lease_seconds: int = 30,
    now: datetime | None = None,
) -> list[ClaimedOutboxItem]:
    """Claim due outbox rows using SKIP LOCKED, including stale crashed PROCESSING leases."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    stmt = (
        select(OutboxEvent)
        .where(
            or_(
                and_(
                    OutboxEvent.status.in_(("PENDING", "FAILED_RETRYABLE")),
                    OutboxEvent.next_attempt_at <= current,
                ),
                and_(
                    OutboxEvent.status == "PROCESSING",
                    OutboxEvent.next_attempt_at <= current,
                ),
            )
        )
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    rows = list(session.execute(stmt).scalars().all())
    claimed: list[ClaimedOutboxItem] = []

    for row in rows:
        job_id = row.job_run_id or row.aggregate_id
        linked_job = session.get(JobRun, job_id)
        if linked_job is not None and linked_job.status in {
            "SUCCEEDED",
            "FAILED_FINAL",
            "CANCELLED",
        }:
            # Do not re-dispatch an already terminal JobRun after a crashed dispatcher lease.
            row.status = "PUBLISHED"
            row.published_at = current
            row.last_error_code = None
            row.last_error_message = None
            continue

        row.status = "PROCESSING"
        row.attempt_count += 1
        row.next_attempt_at = current + timedelta(seconds=claim_lease_seconds)

        payload: dict[str, Any] = dict(row.payload or {})
        claimed.append(
            ClaimedOutboxItem(
                outbox_id=row.id,
                job_run_id=job_id,
                task_name=str(payload.get("task_name", "")),
                queue_name=str(payload.get("queue_name", "control")),
                attempt_count=row.attempt_count,
                maximum_attempts=row.maximum_attempts,
                request_id=str(payload.get("request_id", "-")),
            )
        )

    session.flush()
    return claimed


def dispatch_outbox_batch(
    session_factory: sessionmaker[Session],
    *,
    publisher: TaskPublisher,
    batch_size: int = 10,
    claim_lease_seconds: int = 30,
    now: datetime | None = None,
) -> OutboxDispatchStats:
    """Claim due outbox events, publish to Celery, and record published/retry outcome."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    with session_scope(session_factory) as session:
        claimed = claim_outbox_batch(
            session,
            batch_size=batch_size,
            claim_lease_seconds=claim_lease_seconds,
            now=current,
        )

    if not claimed:
        return OutboxDispatchStats(claimed=0, published=0, retried=0, dead_lettered=0)

    published_count = 0
    retried_count = 0
    dead_count = 0

    for item in claimed:
        try:
            publisher.publish_job(
                job_run_id=item.job_run_id,
                task_name=item.task_name,
                queue_name=item.queue_name,
                attempt=item.attempt_count,
                request_id=item.request_id,
            )
            with session_scope(session_factory) as session:
                outbox = session.execute(
                    select(OutboxEvent).where(OutboxEvent.id == item.outbox_id).with_for_update()
                ).scalar_one()
                # Guard against a fast worker already completing and re-arming this outbox row
                # (`status='PENDING'`) before this post-publish transaction acquires the lock.
                if outbox.status == "PROCESSING" and outbox.attempt_count == item.attempt_count:
                    outbox.status = "PUBLISHED"
                    outbox.published_at = datetime.now(UTC)
                    outbox.last_error_code = None
                    outbox.last_error_message = None

                    job = session.execute(
                        select(JobRun).where(JobRun.id == item.job_run_id).with_for_update()
                    ).scalar_one_or_none()
                    if job is not None and job.status == "PENDING":
                        job.status = "DISPATCHED"
            published_count += 1
        except Exception as exc:
            err_code = f"DISPATCH_{type(exc).__name__.upper()}"[:64]
            err_msg = redact_text(str(exc) or type(exc).__name__)[:500]
            logger.warning(
                "outbox_dispatch_failed",
                extra={"outbox_id": str(item.outbox_id), "error_category": err_code},
            )
            with session_scope(session_factory) as session:
                outbox = session.execute(
                    select(OutboxEvent).where(OutboxEvent.id == item.outbox_id).with_for_update()
                ).scalar_one()
                if outbox.status != "PROCESSING" or outbox.attempt_count != item.attempt_count:
                    continue

                outbox.last_error_code = err_code
                outbox.last_error_message = err_msg

                if item.attempt_count >= item.maximum_attempts:
                    outbox.status = "DEAD_LETTER"
                    job = session.execute(
                        select(JobRun).where(JobRun.id == item.job_run_id).with_for_update()
                    ).scalar_one_or_none()
                    if job is not None and job.status in {
                        "PENDING",
                        "DISPATCHED",
                        "FAILED_RETRYABLE",
                    }:
                        job.status = "FAILED_FINAL"
                        job.worker_finished_at = datetime.now(UTC)
                        job.last_error_code = err_code
                        job.last_error_message = err_msg
                    dead_count += 1
                else:
                    delay = compute_backoff_seconds(item.attempt_count, base_seconds=2.0)
                    outbox.status = "FAILED_RETRYABLE"
                    outbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
                    retried_count += 1

    return OutboxDispatchStats(
        claimed=len(claimed),
        published=published_count,
        retried=retried_count,
        dead_lettered=dead_count,
    )
