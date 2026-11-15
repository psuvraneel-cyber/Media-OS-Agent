"""Integration (disposable DB): job-history purge deletes only old finished recurring runs."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from media_os_api.db import (
    JobRun,
    OutboxEvent,
    ScheduleDefinition,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.purge import purge_job_history

pytestmark = pytest.mark.integration
TASK = "media_os.test.purge_probe"


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def factory() -> Iterator[Any]:
    engine = create_db_engine(_db_url(), pool_size=2, max_overflow=1)
    f = create_session_factory(engine)
    try:
        yield f
    finally:
        with session_scope(f) as db:
            db.execute(delete(JobRun).where(JobRun.task_name == TASK))
            db.execute(delete(ScheduleDefinition).where(ScheduleDefinition.task_name == TASK))
        engine.dispose()


def _run(
    db: Any, *, sched: uuid.UUID | None, status: str, finished: datetime, task: str = TASK
) -> uuid.UUID:
    job = JobRun(
        task_name=task,
        task_type="TEST",
        queue_name="control",
        payload={},
        idempotency_key=f"purge-{uuid.uuid4().hex}",
        status=status,
        available_at=finished,
        worker_finished_at=finished,
        schedule_definition_id=sched,
    )
    db.add(job)
    db.flush()
    db.add(
        OutboxEvent(
            aggregate_type="JobRun",
            aggregate_id=job.id,
            event_type="JOB_DISPATCH",
            payload={},
            status="PUBLISHED",
            job_run_id=job.id,
            next_attempt_at=finished,
        )
    )
    db.flush()
    return job.id


def test_purges_only_old_finished_recurring_runs(factory: Any) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        sched = ScheduleDefinition(
            name=f"purge-probe-{uuid.uuid4().hex[:6]}",
            task_name=TASK,
            task_type="TEST",
            schedule_config={"kind": "interval", "seconds": 900},
            payload_template={},
            timezone="Asia/Kolkata",
            next_run_at=now + timedelta(days=1),
            is_enabled=False,
        )
        db.add(sched)
        db.flush()
        old_ok = _run(db, sched=sched.id, status="SUCCEEDED", finished=now - timedelta(days=40))
        older_ok = _run(db, sched=sched.id, status="SUCCEEDED", finished=now - timedelta(days=41))
        recent_ok = _run(db, sched=sched.id, status="SUCCEEDED", finished=now - timedelta(days=5))
        old_fail = _run(
            db, sched=sched.id, status="FAILED_FINAL", finished=now - timedelta(days=40)
        )
        ancient_fail = _run(
            db, sched=sched.id, status="FAILED_FINAL", finished=now - timedelta(days=120)
        )
        manual_old = _run(db, sched=None, status="SUCCEEDED", finished=now - timedelta(days=400))
        # A different recurring task whose only finished run is old: it is the newest, so kept.
        lonely = _run(
            db,
            sched=sched.id,
            status="SUCCEEDED",
            finished=now - timedelta(days=60),
            task=TASK,
        )
    with session_scope(factory) as db:
        out = purge_job_history(db, now=now)
    with session_scope(factory) as db:
        left = set(db.execute(select(JobRun.id).where(JobRun.task_name == TASK)).scalars())
        outbox_left = set(
            db.execute(
                select(OutboxEvent.job_run_id).where(OutboxEvent.job_run_id.in_(left))
            ).scalars()
        )
    assert recent_ok in left  # inside 30 days
    assert old_fail in left  # failures kept 90 days
    assert manual_old in left  # owner-triggered runs are never purged
    assert old_ok not in left and older_ok not in left and ancient_fail not in left
    assert lonely not in left  # newest finished run of TASK is recent_ok, not this one
    assert out["deleted_job_runs"] == 4 and out["deleted_outbox_events"] == 4
    assert outbox_left == left


def test_keeps_newest_finished_run_of_each_task(factory: Any) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        sched = ScheduleDefinition(
            name=f"purge-probe-{uuid.uuid4().hex[:6]}",
            task_name=TASK,
            task_type="TEST",
            schedule_config={"kind": "interval", "seconds": 900},
            payload_template={},
            timezone="Asia/Kolkata",
            next_run_at=now + timedelta(days=1),
            is_enabled=False,
        )
        db.add(sched)
        db.flush()
        only = _run(db, sched=sched.id, status="SUCCEEDED", finished=now - timedelta(days=200))
    with session_scope(factory) as db:
        purge_job_history(db, now=now)
    with session_scope(factory) as db:
        assert db.get(JobRun, only) is not None  # "last successful sync" never goes blank
