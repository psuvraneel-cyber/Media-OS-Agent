"""Milestone 5 integration: admin CLI, disabled-by-default schedules and research queue routing,
plus a scheduled fetch run end to end through the durable job ledger (fixture upstream).
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker
from tests.research_fixtures import HackerNewsFixtureUpstream, make_hn_http

from media_os_api.db import (
    AuditEvent,
    JobRun,
    OutboxEvent,
    ResearchSource,
    ResearchSourceCheckpoint,
    ScheduleDefinition,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.ledger import execute_durable_job
from media_os_api.jobs.scheduler import run_scheduler_tick
from media_os_api.research import admin
from media_os_api.research.fetchers.base import SourceFetcher
from media_os_api.research.http import SafeHttpClient
from media_os_api.research.jobs import fetch_task_name, make_fetch_handler

pytestmark = pytest.mark.integration
HN = "HACKERNEWS"


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_db_engine(_db_url(), pool_size=4, max_overflow=2)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _reset(factory: sessionmaker[Session]) -> Iterator[None]:
    def reset() -> None:
        with session_scope(factory) as db:
            job_ids = (
                db.execute(
                    select(JobRun.id).where(JobRun.task_name.like("media_os.research.fetch.%"))
                )
                .scalars()
                .all()
            )
            if job_ids:
                db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(job_ids)))
                db.execute(delete(JobRun).where(JobRun.id.in_(job_ids)))
            db.execute(delete(ResearchSourceCheckpoint))
            db.execute(
                update(ResearchSource)
                .where(ResearchSource.source_code != "REDDIT")
                .values(
                    policy_mode="OFF",
                    live_fetch_approved=False,
                    approved_by=None,
                    approved_at=None,
                    health_status="DISABLED",
                    consecutive_failures=0,
                    backoff_until=None,
                )
            )
            db.execute(
                update(ScheduleDefinition)
                .where(ScheduleDefinition.name.like("research.fetch.%"))
                .values(is_enabled=False)
            )

    reset()
    yield
    reset()  # leave every schedule disabled for other test modules


def test_admin_flow_and_guards(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with pytest.raises(admin.AdminError, match="BLOCKED"):
        admin.set_mode(factory, "REDDIT", "ENABLED", now)
    with pytest.raises(admin.AdminError, match="set-mode EXPERIMENTAL first"):
        admin.approve_live(factory, HN, "owner", now)
    with pytest.raises(admin.AdminError, match="approved before"):
        admin.set_schedule(factory, HN, True, now)

    admin.set_mode(factory, HN, "EXPERIMENTAL", now)
    admin.approve_live(factory, HN, "owner", now)
    admin.set_schedule(factory, HN, True, now)
    row = next(r for r in admin.status(factory) if r["source"] == HN)
    assert row["mode"] == "EXPERIMENTAL" and row["live_fetch_approved"] is True
    assert row["schedule_enabled"] is True
    assert datetime.fromisoformat(row["next_run_at"]) > now  # next slot, no burst

    admin.set_mode(factory, HN, "OFF", now)  # switching off withdraws approval and schedule
    row = next(r for r in admin.status(factory) if r["source"] == HN)
    assert (row["mode"], row["live_fetch_approved"], row["schedule_enabled"]) == (
        "OFF",
        False,
        False,
    )

    with session_scope(factory) as db:
        actions = set(
            db.execute(
                select(AuditEvent.action).where(
                    AuditEvent.target_type == "ResearchSource", AuditEvent.timestamp >= now
                )
            ).scalars()
        )
    assert {
        "RESEARCH_SOURCE_MODE_CHANGED",
        "RESEARCH_LIVE_FETCH_APPROVED",
        "RESEARCH_SCHEDULE_ENABLED",
    } <= actions


def test_schedules_stay_disabled_by_default(factory: sessionmaker[Session]) -> None:
    run_scheduler_tick(factory, now=datetime.now(UTC) + timedelta(days=3))
    with session_scope(factory) as db:
        jobs = (
            db.execute(select(JobRun).where(JobRun.task_name.like("media_os.research.fetch.%")))
            .scalars()
            .all()
        )
    assert jobs == []


def test_enabled_schedule_routes_to_research_queue_and_executes(
    factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    admin.set_mode(factory, HN, "EXPERIMENTAL", now)
    admin.approve_live(factory, HN, "owner", now)
    admin.set_schedule(factory, HN, True, now)

    run_scheduler_tick(factory, now=now + timedelta(hours=2))
    with session_scope(factory) as db:
        job = db.execute(select(JobRun).where(JobRun.task_name == fetch_task_name(HN))).scalar_one()
        outbox = db.execute(
            select(OutboxEvent).where(OutboxEvent.job_run_id == job.id)
        ).scalar_one()
        assert job.queue_name == "research" and outbox.payload["queue_name"] == "research"
        assert job.payload["source"] == HN and "items" not in job.payload  # content-free
        job_id = job.id

    upstream = HackerNewsFixtureUpstream()

    def http(_f: SourceFetcher, _c: Mapping[str, Any]) -> SafeHttpClient:
        return make_hn_http(upstream)

    handler = make_fetch_handler(
        http_factory=http, session_factory_provider=lambda _s: factory, env_enabled=True
    )
    result = execute_durable_job(
        factory, job_run_id=job_id, registry={fetch_task_name(HN): handler}, lease_seconds=900
    )
    assert result["status"] == "SUCCEEDED"
    assert result["result"]["status"] == "SUCCEEDED" and result["result"]["trigger"] == "schedule"
    assert upstream.paths  # fixture upstream was used; no real network


def test_kill_switch_off_fails_job_permanently_without_http(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    admin.set_mode(factory, HN, "EXPERIMENTAL", now)
    admin.approve_live(factory, HN, "owner", now)
    job_id = admin.run_now(factory, HN, now)
    upstream = HackerNewsFixtureUpstream()

    def http(_f: SourceFetcher, _c: Mapping[str, Any]) -> SafeHttpClient:
        return make_hn_http(upstream)

    handler = make_fetch_handler(
        http_factory=http, session_factory_provider=lambda _s: factory, env_enabled=False
    )
    result = execute_durable_job(
        factory, job_run_id=job_id, registry={fetch_task_name(HN): handler}, lease_seconds=900
    )
    assert result["status"] == "FAILED_FINAL" and result["error_code"] == "LIVE_FETCH_KILL_SWITCH"
    assert upstream.paths == []
