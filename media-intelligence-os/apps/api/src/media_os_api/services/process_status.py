"""Live status of the Media OS processes, worked out from the job ledger.

The API can't see other containers directly, but every process leaves a trail in `job_runs`:

* **Scheduler:** it creates the watchdog job every 5 minutes (and Telegram polling every minute),
  so a recently *created* scheduled job means it's running.
* **Worker:** it finishes jobs on the control/default/research queues all the time, so a recently
  *finished* job there means it's running. A job waiting far past its start time means it isn't.
* **Creative renderer:** it only works on demand, so with nothing to do it's "idle". A render job
  that has waited more than 10 minutes means the renderer is down or stuck.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from media_os_api.db.models import JobRun, ResearchSource
from media_os_api.jobs.routing import CREATIVE_QUEUE

State = Literal["running", "idle", "stalled", "down", "off", "unknown"]

SCHEDULER_FRESH = timedelta(minutes=12)
WORKER_FRESH = timedelta(minutes=12)
BACKLOG_AFTER = timedelta(minutes=10)
WAITING = ("PENDING", "DISPATCHED")


@dataclass(frozen=True)
class ProcessState:
    key: str
    state: State
    detail: str
    last_activity_at: datetime | None = None


def _ago(then: datetime | None, now: datetime) -> str:
    if then is None:
        return "never"
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} days ago"


def classify_scheduler(last_created: datetime | None, now: datetime) -> ProcessState:
    if last_created is None:
        return ProcessState("scheduler", "unknown", "No scheduled jobs yet.")
    fresh = now - last_created <= SCHEDULER_FRESH
    return ProcessState(
        "scheduler",
        "running" if fresh else "down",
        f"Last queued a scheduled job {_ago(last_created, now)}.",
        last_created,
    )


def classify_queue(
    key: str,
    *,
    last_finished: datetime | None,
    oldest_waiting: datetime | None,
    now: datetime,
    on_demand: bool,
) -> ProcessState:
    """`oldest_waiting` is the earliest `available_at` of a job still waiting to start."""
    if oldest_waiting is not None and now - oldest_waiting > BACKLOG_AFTER:
        return ProcessState(
            key,
            "stalled",
            f"A job has waited {_ago(oldest_waiting, now).replace(' ago', '')} to start.",
            last_finished,
        )
    if last_finished is None:
        return ProcessState(key, "idle" if on_demand else "unknown", "No finished jobs yet.", None)
    if on_demand:
        return ProcessState(
            key, "idle", f"Last finished a job {_ago(last_finished, now)}.", last_finished
        )
    fresh = now - last_finished <= WORKER_FRESH
    return ProcessState(
        key,
        "running" if fresh else "down",
        f"Last finished a job {_ago(last_finished, now)}.",
        last_finished,
    )


def process_states(db: Session, now: datetime) -> list[ProcessState]:
    last_scheduled = db.execute(
        select(func.max(JobRun.created_at)).where(JobRun.schedule_definition_id.is_not(None))
    ).scalar_one()

    def last_finished(cond: ColumnElement[bool]) -> datetime | None:
        return db.execute(select(func.max(JobRun.worker_finished_at)).where(cond)).scalar_one()

    def oldest_waiting(cond: ColumnElement[bool]) -> datetime | None:
        return db.execute(
            select(func.min(JobRun.available_at)).where(JobRun.status.in_(WAITING), cond)
        ).scalar_one()

    worker_q = JobRun.queue_name != CREATIVE_QUEUE
    creative_q = JobRun.queue_name == CREATIVE_QUEUE
    enabled = db.execute(
        select(func.count())
        .select_from(ResearchSource)
        .where(ResearchSource.policy_mode.in_(("EXPERIMENTAL", "ENABLED")))
    ).scalar_one()
    research = ProcessState(
        "research",
        "running" if enabled else "off",
        f"{enabled} source(s) switched on." if enabled else "All sources are switched off.",
    )
    return [
        classify_scheduler(last_scheduled, now),
        classify_queue(
            "worker",
            last_finished=last_finished(worker_q),
            oldest_waiting=oldest_waiting(worker_q),
            now=now,
            on_demand=False,
        ),
        classify_queue(
            "creative",
            last_finished=last_finished(creative_q),
            oldest_waiting=oldest_waiting(creative_q),
            now=now,
            on_demand=True,
        ),
        research,
    ]
