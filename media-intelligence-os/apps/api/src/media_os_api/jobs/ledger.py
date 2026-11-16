"""Durable PostgreSQL job ledger, idempotent claiming, lease recovery, and retry tracking."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db.models import JobRun, OutboxEvent
from media_os_api.db.session import session_scope
from media_os_api.jobs.errors import (
    JobIdempotencyConflictError,
    PermanentTaskError,
    classify_exception,
    compute_backoff_seconds,
    validate_safe_task_payload,
)

TERMINAL_JOB_STATUSES = frozenset({"SUCCEEDED", "FAILED_FINAL", "CANCELLED"})

ITEMS_REDACTED_MARKER = "items_redacted"


def items_digest(items: Any) -> str:
    """Stable SHA-256 over a JSON-serialisable item list (used to identify scrubbed payloads)."""
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_items_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return `payload` with bulk `items` replaced by a count + digest.

    Used to remove untrusted third-party content from the durable job ledger once it is no longer
    needed for retries. Idempotent: an already-redacted payload is returned unchanged.
    """
    if payload.get(ITEMS_REDACTED_MARKER) is True:
        return dict(payload)
    items = payload.get("items") or []
    redacted = {k: v for k, v in payload.items() if k != "items"}
    redacted["item_count"] = len(items) if isinstance(items, list) else 0
    redacted["items_sha256"] = str(payload.get("items_sha256") or items_digest(items))
    redacted[ITEMS_REDACTED_MARKER] = True
    return redacted


def _payloads_equivalent(existing: Mapping[str, Any] | None, new: Mapping[str, Any]) -> bool:
    """Exact match, or `existing` is the redacted form of `new` (same items digest)."""
    current = dict(existing or {})
    if current == dict(new):
        return True
    return current.get(ITEMS_REDACTED_MARKER) is True and current == redact_items_payload(new)


@dataclass(frozen=True)
class JobClaimResult:
    """Outcome of attempting to claim a JobRun for execution."""

    should_execute: bool
    already_completed: bool
    leased_elsewhere: bool
    job_run_id: uuid.UUID
    task_name: str
    payload: dict[str, Any]
    existing_result: dict[str, Any] | None
    status: str


TaskHandler = Callable[[Session, JobRun], dict[str, Any]]


def enqueue_durable_job(
    session: Session,
    *,
    task_name: str,
    task_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    brand_profile_id: uuid.UUID | None = None,
    schedule_definition_id: uuid.UUID | None = None,
    queue_name: str = "control",
    priority: int = 5,
    maximum_attempts: int = 3,
    available_at: datetime | None = None,
    request_id: str = "-",
) -> tuple[JobRun, bool]:
    """Create a JobRun and its matching OutboxEvent atomically inside the caller transaction.

    Returns `(job_run, created)` where `created=False` indicates safe idempotent reuse.
    """
    safe_payload = validate_safe_task_payload(payload)
    now = datetime.now(UTC)
    run_at = (available_at or now).astimezone(UTC)

    existing = session.execute(
        select(JobRun).where(JobRun.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.task_name != task_name
            or existing.task_type != task_type
            or not _payloads_equivalent(existing.payload, safe_payload)
        ):
            raise JobIdempotencyConflictError(
                f"Idempotency key '{idempotency_key}' already bound to a different job."
            )
        return existing, False

    try:
        with session.begin_nested():
            job = JobRun(
                brand_profile_id=brand_profile_id,
                schedule_definition_id=schedule_definition_id,
                task_name=task_name,
                task_type=task_type,
                queue_name=queue_name,
                payload=safe_payload,
                idempotency_key=idempotency_key,
                status="PENDING",
                priority=priority,
                attempt_count=0,
                maximum_attempts=maximum_attempts,
                available_at=run_at,
            )
            session.add(job)
            session.flush()

            outbox = OutboxEvent(
                job_run_id=job.id,
                event_type="JOB_DISPATCH",
                aggregate_type="JobRun",
                aggregate_id=job.id,
                payload={
                    "job_run_id": str(job.id),
                    "task_name": task_name,
                    "queue_name": queue_name,
                    "request_id": request_id,
                    "idempotency_key": idempotency_key,
                },
                status="PENDING",
                attempt_count=0,
                maximum_attempts=max(5, maximum_attempts * 2),
                next_attempt_at=run_at,
            )
            session.add(outbox)
            session.flush()
            return job, True
    except IntegrityError as exc:
        existing_after_race = session.execute(
            select(JobRun).where(JobRun.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing_after_race is None:
            raise exc
        if (
            existing_after_race.task_name != task_name
            or existing_after_race.task_type != task_type
            or not _payloads_equivalent(existing_after_race.payload, safe_payload)
        ):
            raise JobIdempotencyConflictError(
                f"Idempotency key '{idempotency_key}' already bound to a different job."
            ) from exc
        return existing_after_race, False


def claim_job_run_for_execution(
    session: Session,
    *,
    job_run_id: uuid.UUID,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> JobClaimResult:
    """Atomically acquire an execution lease on a JobRun, rejecting duplicate active/done runs."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    job = session.execute(
        select(JobRun).where(JobRun.id == job_run_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise PermanentTaskError(f"JobRun {job_run_id} does not exist.", code="JOB_NOT_FOUND")

    if job.status in TERMINAL_JOB_STATUSES:
        return JobClaimResult(
            should_execute=False,
            already_completed=True,
            leased_elsewhere=False,
            job_run_id=job.id,
            task_name=job.task_name,
            payload=dict(job.payload),
            existing_result=job.result_payload,
            status=job.status,
        )

    if (
        job.status == "RUNNING"
        and job.lease_expires_at is not None
        and job.lease_expires_at > current
    ):
        return JobClaimResult(
            should_execute=False,
            already_completed=False,
            leased_elsewhere=True,
            job_run_id=job.id,
            task_name=job.task_name,
            payload=dict(job.payload),
            existing_result=None,
            status=job.status,
        )

    if job.attempt_count >= job.maximum_attempts:
        job.status = "FAILED_FINAL"
        job.worker_finished_at = current
        job.lease_expires_at = None
        if not job.last_error_code:
            job.last_error_code = "MAX_ATTEMPTS_EXCEEDED"
            job.last_error_message = "Job exceeded maximum allowed attempts."
        session.flush()
        return JobClaimResult(
            should_execute=False,
            already_completed=True,
            leased_elsewhere=False,
            job_run_id=job.id,
            task_name=job.task_name,
            payload=dict(job.payload),
            existing_result=None,
            status=job.status,
        )

    job.attempt_count += 1
    job.status = "RUNNING"
    job.worker_started_at = current
    job.lease_expires_at = current + timedelta(seconds=lease_seconds)
    session.flush()

    return JobClaimResult(
        should_execute=True,
        already_completed=False,
        leased_elsewhere=False,
        job_run_id=job.id,
        task_name=job.task_name,
        payload=dict(job.payload),
        existing_result=None,
        status=job.status,
    )


def complete_job_success(
    session: Session,
    *,
    job_run_id: uuid.UUID,
    result: dict[str, Any],
    now: datetime | None = None,
) -> JobRun:
    """Record successful completion and clear active lease."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    job = session.execute(
        select(JobRun).where(JobRun.id == job_run_id).with_for_update()
    ).scalar_one()
    job.status = "SUCCEEDED"
    job.worker_finished_at = current
    job.lease_expires_at = None
    job.last_error_code = None
    job.last_error_message = None
    job.result_payload = result
    session.flush()
    return job


def complete_job_failure(
    session: Session,
    *,
    job_run_id: uuid.UUID,
    exc: Exception,
    now: datetime | None = None,
    base_backoff_seconds: float = 2.0,
) -> JobRun:
    """Classify failure and either schedule bounded outbox retry or mark FAILED_FINAL."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    classification = classify_exception(exc)

    job = session.execute(
        select(JobRun).where(JobRun.id == job_run_id).with_for_update()
    ).scalar_one()
    job.lease_expires_at = None
    job.last_error_code = classification.code
    job.last_error_message = classification.sanitized_message

    if classification.retryable and job.attempt_count < job.maximum_attempts:
        delay = compute_backoff_seconds(job.attempt_count, base_seconds=base_backoff_seconds)
        next_at = current + timedelta(seconds=delay)
        job.status = "FAILED_RETRYABLE"
        job.available_at = next_at

        # Re-arm the linked transactional outbox event so retry survives Redis/worker restarts
        outbox = session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == "JobRun",
                OutboxEvent.aggregate_id == job.id,
                OutboxEvent.event_type == "JOB_DISPATCH",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if outbox is not None:
            outbox.status = "PENDING"
            outbox.attempt_count = 0
            outbox.next_attempt_at = next_at
            outbox.last_error_code = classification.code
            outbox.last_error_message = classification.sanitized_message
    else:
        job.status = "FAILED_FINAL"
        job.worker_finished_at = current
        # Tell the owner a job gave up (alert jobs themselves are excluded to avoid loops).
        from media_os_api.notify.service import alert_job_failed

        alert_job_failed(session, job)

    session.flush()
    return job


def execute_durable_job(
    session_factory: sessionmaker[Session],
    *,
    job_run_id: uuid.UUID,
    registry: Mapping[str, TaskHandler],
    lease_seconds: int = 120,
    base_backoff_seconds: float = 2.0,
) -> dict[str, Any]:
    """Run a durable job inside explicit DB lease/completion transactions."""
    with session_scope(session_factory) as session:
        claim = claim_job_run_for_execution(
            session,
            job_run_id=job_run_id,
            lease_seconds=lease_seconds,
        )
        if not claim.should_execute:
            return {
                "job_run_id": str(job_run_id),
                "status": claim.status,
                "duplicate_skipped": True,
                "result": claim.existing_result,
            }

    handler = registry.get(claim.task_name)
    try:
        if handler is None:
            raise PermanentTaskError(
                f"No handler registered for task_name '{claim.task_name}'.",
                code="UNREGISTERED_TASK",
            )
        with session_scope(session_factory) as exec_session:
            job = exec_session.execute(select(JobRun).where(JobRun.id == job_run_id)).scalar_one()
            result = handler(exec_session, job)
            complete_job_success(exec_session, job_run_id=job_run_id, result=result)
            return {
                "job_run_id": str(job_run_id),
                "status": "SUCCEEDED",
                "duplicate_skipped": False,
                "result": result,
            }
    except Exception as exc:
        with session_scope(session_factory) as err_session:
            failed_job = complete_job_failure(
                err_session,
                job_run_id=job_run_id,
                exc=exc,
                base_backoff_seconds=base_backoff_seconds,
            )
            return {
                "job_run_id": str(job_run_id),
                "status": failed_job.status,
                "duplicate_skipped": False,
                "error_code": failed_job.last_error_code,
            }
