"""Job `media_os.maintenance.purge_job_history`: bound the growth of job_runs / outbox_events.

Recurring schedules create a JobRun (and its dispatch OutboxEvent) on every tick — the publish
status sync alone adds ~100 rows a day. This purge deletes only:

* runs created by a recurring schedule (`schedule_definition_id IS NOT NULL`); one-off runs the
  owner triggered (schedule a carousel, render, generate) are never touched,
* that finished (SUCCEEDED, FAILED_FINAL, CANCELLED),
* longer ago than the retention window (succeeded/cancelled: 30 days; failed: 90 days, so
  failures stay available for diagnosis),

and always keeps the most recent finished run of each task, so "last successful sync" readouts
never go blank. Their outbox events are deleted with them. It works in bounded batches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from media_os_api.db.models import JobRun, OutboxEvent

PURGE_TASK = "media_os.maintenance.purge_job_history"
KEEP_SUCCEEDED = timedelta(days=30)
KEEP_FAILED = timedelta(days=90)
BATCH = 5000


def purge_job_history(
    session: Session,
    *,
    now: datetime | None = None,
    keep_succeeded: timedelta = KEEP_SUCCEEDED,
    keep_failed: timedelta = KEEP_FAILED,
    batch: int = BATCH,
) -> dict[str, int]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    finished_at = func.coalesce(JobRun.worker_finished_at, JobRun.updated_at)

    # Newest finished run per task is always kept.
    newest = (
        select(JobRun.task_name, func.max(finished_at).label("at"))
        .where(JobRun.status.in_(("SUCCEEDED", "FAILED_FINAL", "CANCELLED")))
        .group_by(JobRun.task_name)
        .subquery()
    )
    keep_ids = set(
        session.execute(
            select(JobRun.id).join(
                newest, (JobRun.task_name == newest.c.task_name) & (finished_at == newest.c.at)
            )
        ).scalars()
    )

    candidates = list(
        session.execute(
            select(JobRun.id)
            .where(
                JobRun.schedule_definition_id.is_not(None),
                or_(
                    JobRun.status.in_(("SUCCEEDED", "CANCELLED"))
                    & (finished_at < current - keep_succeeded),
                    (JobRun.status == "FAILED_FINAL") & (finished_at < current - keep_failed),
                ),
            )
            .order_by(finished_at)
            .limit(batch)
        ).scalars()
    )
    ids = [i for i in candidates if i not in keep_ids]
    if not ids:
        return {"deleted_job_runs": 0, "deleted_outbox_events": 0}
    outbox = session.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(ids)))
    runs = session.execute(delete(JobRun).where(JobRun.id.in_(ids)))
    session.flush()
    return {
        "deleted_job_runs": int(getattr(runs, "rowcount", 0) or 0),
        "deleted_outbox_events": int(getattr(outbox, "rowcount", 0) or 0),
    }


def handle_purge_job_history(session: Session, job: JobRun) -> dict[str, Any]:
    return purge_job_history(session)
