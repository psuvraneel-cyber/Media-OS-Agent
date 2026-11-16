"""PostgreSQL-backed persistent scheduler, timezone/DST calculator, and stale-lease recovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db.models import JobRun, ScheduleDefinition
from media_os_api.db.session import session_scope
from media_os_api.jobs.errors import RetryableTaskError
from media_os_api.jobs.ledger import complete_job_failure, enqueue_durable_job
from media_os_api.jobs.routing import queue_for_task, retry_policy_for_task

logger = logging.getLogger("media_os_api.scheduler")


@dataclass(frozen=True)
class SchedulerTickResult:
    """Summary of one persistent scheduler polling tick."""

    schedules_triggered: int
    jobs_created: int
    jobs_deduped: int
    stale_leases_recovered: int


def compute_next_run_at(
    schedule_config: dict[str, Any],
    timezone_name: str,
    *,
    previous_run: datetime,
    now: datetime,
) -> datetime:
    """Compute the next strictly-future UTC execution timestamp honoring timezone and DST.

    If multiple occurrences were missed while asleep/offline, skips directly past `now`
    so missed runs never burst-fire simultaneously on recovery.
    """
    tz = ZoneInfo(timezone_name)
    prev_utc = previous_run.astimezone(UTC)
    now_utc = now.astimezone(UTC)
    floor_utc = max(prev_utc, now_utc)

    kind = str(schedule_config.get("kind", "")).strip().lower()
    if kind == "interval":
        seconds = int(schedule_config.get("seconds", 0))
        if seconds < 1:
            raise ValueError("Interval schedule requires seconds >= 1.")
        step = timedelta(seconds=seconds)
        candidate = prev_utc + step
        if candidate <= floor_utc:
            elapsed = (floor_utc - prev_utc).total_seconds()
            jumps = int(elapsed // seconds) + 1
            candidate = prev_utc + timedelta(seconds=jumps * seconds)
        return candidate

    if kind == "daily_time":
        hour = int(schedule_config.get("hour", 0))
        minute = int(schedule_config.get("minute", 0))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("daily_time schedule requires valid hour (0-23) and minute (0-59).")

        anchor_local = floor_utc.astimezone(tz)
        day_cursor = anchor_local.date()
        for _ in range(8):
            candidate_local = datetime(
                day_cursor.year,
                day_cursor.month,
                day_cursor.day,
                hour,
                minute,
                0,
                tzinfo=tz,
                fold=0,
            )
            candidate_utc = candidate_local.astimezone(UTC)
            if candidate_utc > floor_utc:
                return candidate_utc
            day_cursor += timedelta(days=1)
        raise ValueError("Unable to compute next daily_time occurrence.")

    raise ValueError(f"Unsupported schedule_config kind: {kind!r}")


def run_scheduler_tick(
    session_factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
    batch_size: int = 20,
) -> SchedulerTickResult:
    """Find due ScheduleDefinitions with SKIP LOCKED and atomically enqueue 1 JobRun each."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    triggered = 0
    created_count = 0
    deduped_count = 0

    with session_scope(session_factory) as session:
        due_schedules = list(
            session.execute(
                select(ScheduleDefinition)
                .where(
                    ScheduleDefinition.is_enabled.is_(True),
                    ScheduleDefinition.next_run_at <= current,
                )
                .order_by(ScheduleDefinition.next_run_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )

        for sched in due_schedules:
            try:
                with session.begin_nested():
                    scheduled_slot_utc = sched.next_run_at.astimezone(UTC)
                    occurrence_key = f"schedule:{sched.id}:{scheduled_slot_utc.isoformat()}"
                    payload = dict(sched.payload_template or {})
                    payload.setdefault("schedule_id", str(sched.id))
                    payload.setdefault("scheduled_for_utc", scheduled_slot_utc.isoformat())

                    next_run = compute_next_run_at(
                        sched.schedule_config,
                        sched.timezone,
                        previous_run=scheduled_slot_utc,
                        now=current,
                    )
                    _job, created = enqueue_durable_job(
                        session,
                        task_name=sched.task_name,
                        task_type=sched.task_type,
                        payload=payload,
                        idempotency_key=occurrence_key,
                        brand_profile_id=sched.brand_profile_id,
                        schedule_definition_id=sched.id,
                        queue_name=queue_for_task(sched.task_name),
                        maximum_attempts=retry_policy_for_task(sched.task_name).maximum_attempts,
                        available_at=current,
                    )
                    sched.last_run_at = scheduled_slot_utc
                    sched.next_run_at = next_run
                triggered += 1
                if created:
                    created_count += 1
                else:
                    deduped_count += 1
            except (ValueError, ZoneInfoNotFoundError) as exc:
                logger.error(
                    "scheduler_invalid_schedule_disabled",
                    extra={
                        "schedule_id": str(sched.id),
                        "error_category": type(exc).__name__,
                    },
                )
                sched.is_enabled = False

    recovered = recover_stale_leases(session_factory, now=current)
    return SchedulerTickResult(
        schedules_triggered=triggered,
        jobs_created=created_count,
        jobs_deduped=deduped_count,
        stale_leases_recovered=recovered,
    )


def recover_stale_leases(
    session_factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
    batch_size: int = 20,
) -> int:
    """Reclaim RUNNING JobRuns whose worker lease expired after a crash or hang."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    recovered = 0
    with session_scope(session_factory) as session:
        expired_jobs = list(
            session.execute(
                select(JobRun)
                .where(
                    JobRun.status == "RUNNING",
                    JobRun.lease_expires_at.is_not(None),
                    JobRun.lease_expires_at <= current,
                )
                .order_by(JobRun.lease_expires_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for job in expired_jobs:
            complete_job_failure(
                session,
                job_run_id=job.id,
                exc=RetryableTaskError(
                    "Worker lease expired prior to job completion.",
                    code="WORKER_LEASE_EXPIRED",
                ),
                now=current,
            )
            recovered += 1
    return recovered
