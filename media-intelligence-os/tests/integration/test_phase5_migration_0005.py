"""Migration 0005: research source registry, checkpoints, health, and global schedules.

Runs only against the disposable test database (guarded by tests/integration/conftest.py).
The final test exercises downgrade/upgrade and leaves the schema at head.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db import (
    ResearchSource,
    ResearchSourceCheckpoint,
    ScheduleDefinition,
    check_database_health,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.errors import PolicyRefusalError, RetryableTaskError
from media_os_api.jobs.scheduler import run_scheduler_tick
from media_os_api.research.registry import (
    CheckpointState,
    assert_live_fetch_permitted,
    get_research_source,
    load_checkpoint,
    record_fetch_failure,
    record_fetch_success,
    save_checkpoint,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRST_COHORT = {"HACKERNEWS", "GITHUB", "HUGGINGFACE", "ARXIV", "OPENAI_NEWS"}


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_db_engine(_db_url(), pool_size=4, max_overflow=2)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture(autouse=True)
def _reset_registry_state(factory: sessionmaker[Session]) -> None:
    """Return registry rows to their seeded state so tests are order-independent."""
    with session_scope(factory) as db:
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
                last_error_code=None,
                last_error_message=None,
            )
        )


def test_registry_is_seeded_off_and_reddit_blocked(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as db:
        rows = {r.source_code: r for r in db.execute(select(ResearchSource)).scalars()}
    assert set(rows) == FIRST_COHORT | {"REDDIT"}
    for code in FIRST_COHORT:
        assert rows[code].policy_mode == "OFF"
        assert rows[code].live_fetch_approved is False
        assert rows[code].health_status == "DISABLED"
        assert "min_interval_ms" in rows[code].config
    assert rows["REDDIT"].policy_mode == "BLOCKED"
    assert rows["REDDIT"].health_status == "BLOCKED"
    assert rows["ARXIV"].config["min_interval_ms"] >= 3000
    assert rows["OPENAI_NEWS"].config["feed_url"] == "https://openai.com/news/rss.xml"


@pytest.mark.parametrize(
    ("code", "values"),
    [
        ("REDDIT", {"policy_mode": "EXPERIMENTAL"}),  # Reddit lock
        ("REDDIT", {"policy_mode": "ENABLED"}),
        ("HACKERNEWS", {"live_fetch_approved": True}),  # approval while OFF
        ("HACKERNEWS", {"policy_mode": "EXPERIMENTAL", "live_fetch_approved": True}),  # no approver
        ("HACKERNEWS", {"policy_mode": "PAUSED"}),
        ("HACKERNEWS", {"health_status": "GREAT"}),
        ("HACKERNEWS", {"consecutive_failures": -1}),
    ],
)
def test_registry_check_constraints(
    factory: sessionmaker[Session], code: str, values: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError), session_scope(factory) as db:
        db.execute(
            update(ResearchSource).where(ResearchSource.source_code == code).values(**values)
        )


def test_owner_approval_record_satisfies_constraints(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "HACKERNEWS")
            .values(
                policy_mode="EXPERIMENTAL",
                live_fetch_approved=True,
                approved_by="owner:test",
                approved_at=now,
            )
        )
    with session_scope(factory) as db:
        source = get_research_source(db, "HACKERNEWS")
        assert_live_fetch_permitted(source, env_enabled=True, now=now)
        with pytest.raises(PolicyRefusalError) as excinfo:
            assert_live_fetch_permitted(source, env_enabled=False, now=now)
        assert excinfo.value.code == "LIVE_FETCH_KILL_SWITCH"


def test_gate_refuses_seeded_sources(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        for code, expected in (("REDDIT", "SOURCE_POLICY_BLOCKED"), ("GITHUB", "SOURCE_DISABLED")):
            with pytest.raises(PolicyRefusalError) as excinfo:
                assert_live_fetch_permitted(
                    get_research_source(db, code), env_enabled=True, now=now
                )
            assert excinfo.value.code == expected


def test_seeded_research_schedules_are_global_and_disabled(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as db:
        scheds = (
            db.execute(
                select(ScheduleDefinition).where(ScheduleDefinition.name.like("research.fetch.%"))
            )
            .scalars()
            .all()
        )
        assert len(scheds) == 5
        assert {s.payload_template["source"] for s in scheds} == FIRST_COHORT
        for s in scheds:
            assert s.is_enabled is False
            assert s.brand_profile_id is None
            assert s.channel_account_id is None
            assert s.task_type == "RESEARCH_FETCH"
            assert s.task_name == f"media_os.research.fetch.{s.payload_template['source'].lower()}"

    # Even when overdue, disabled schedules never enqueue work.
    result = run_scheduler_tick(factory, now=datetime.now(UTC) + timedelta(days=2))
    with session_scope(factory) as db:
        research_jobs = db.execute(
            text("SELECT count(*) FROM job_runs WHERE task_name LIKE 'media_os.research.fetch.%'")
        ).scalar_one()
    assert research_jobs == 0
    assert result.jobs_created >= 0


def test_global_schedule_constraints(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)

    def _global(name: str, channel: uuid.UUID | None = None) -> ScheduleDefinition:
        return ScheduleDefinition(
            brand_profile_id=None,
            channel_account_id=channel,
            name=name,
            task_name="media_os.internal.ping",
            task_type="TEST",
            schedule_config={"kind": "interval", "seconds": 60},
            next_run_at=now,
            is_enabled=False,
        )

    with pytest.raises(IntegrityError), session_scope(factory) as db:
        db.add(_global("research.fetch.hackernews"))  # duplicate global name

    with pytest.raises(IntegrityError), session_scope(factory) as db:
        db.add(_global(f"tmp-{uuid.uuid4().hex[:8]}", channel=uuid.uuid4()))  # channel w/o brand


def test_checkpoint_upsert_and_fk(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        assert load_checkpoint(db, "ARXIV", "cat:cs.AI") == CheckpointState()
        save_checkpoint(
            db,
            "ARXIV",
            "cat:cs.AI",
            CheckpointState(cursor={"newest": "2026-11-12T00:00:00Z"}, etag='W/"a"'),
            now=now,
        )
    with session_scope(factory) as db:
        save_checkpoint(
            db,
            "ARXIV",
            "cat:cs.AI",
            CheckpointState(cursor={"newest": "2026-11-13T00:00:00Z"}, last_modified="Thu"),
            now=now,
        )
    with session_scope(factory) as db:
        state = load_checkpoint(db, "ARXIV", "cat:cs.AI")
        assert state.cursor == {"newest": "2026-11-13T00:00:00Z"}
        assert state.etag is None and state.last_modified == "Thu"
        rows = db.execute(select(ResearchSourceCheckpoint)).scalars().all()
        assert len(rows) == 1

    with pytest.raises(IntegrityError), session_scope(factory) as db:
        save_checkpoint(db, "NOT_A_SOURCE", "x", CheckpointState(), now=now)


def test_health_transitions_and_backoff(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        s = record_fetch_failure(
            db,
            "HUGGINGFACE",
            error_code="HTTP_503",
            message="upstream unavailable Bearer abcdefghijklmnop",
            now=now,
        )
        assert s.health_status == "DEGRADED" and s.consecutive_failures == 1
        assert s.backoff_until == now + timedelta(seconds=60)
        assert "abcdefghijklmnop" not in (s.last_error_message or "")
        record_fetch_failure(db, "HUGGINGFACE", error_code="HTTP_503", message="x", now=now)
        s = record_fetch_failure(
            db,
            "HUGGINGFACE",
            error_code="HTTP_429",
            message="rate limited",
            now=now,
            retry_after=timedelta(seconds=900),
        )
        assert s.health_status == "FAILING" and s.consecutive_failures == 3
        assert s.backoff_until == now + timedelta(seconds=900)

    with session_scope(factory) as db:
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "HUGGINGFACE")
            .values(
                policy_mode="EXPERIMENTAL",
                live_fetch_approved=True,
                approved_by="o",
                approved_at=now,
            )
        )
    with session_scope(factory) as db:
        with pytest.raises(RetryableTaskError) as excinfo:
            assert_live_fetch_permitted(
                get_research_source(db, "HUGGINGFACE"), env_enabled=True, now=now
            )
        assert excinfo.value.code == "SOURCE_BACKOFF_ACTIVE"
        s = record_fetch_success(db, "HUGGINGFACE", stats={"new_sources": 3}, now=now)
        assert s.health_status == "HEALTHY" and s.consecutive_failures == 0
        assert s.backoff_until is None and s.last_run_stats == {"new_sources": 3}


def test_zz_downgrade_refuses_foreign_global_schedules_then_roundtrips(
    engine: Engine, factory: sessionmaker[Session]
) -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "apps" / "api" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _db_url())
    now = datetime.now(UTC)
    extra = f"owner-global-{uuid.uuid4().hex[:8]}"

    with session_scope(factory) as db:
        db.add(
            ScheduleDefinition(
                brand_profile_id=None,
                name=extra,
                task_name="media_os.internal.ping",
                task_type="TEST",
                schedule_config={"kind": "interval", "seconds": 60},
                next_run_at=now,
                is_enabled=False,
            )
        )

    with pytest.raises(RuntimeError, match="Refusing downgrade"):
        command.downgrade(cfg, "0004_add_brand_topic_assignments")

    ok, _, ver = check_database_health(engine)
    assert ok and ver == "0007_facebook_platform"
    with session_scope(factory) as db:  # failed downgrade rolled back fully
        seeded = db.execute(
            select(ScheduleDefinition).where(ScheduleDefinition.name.like("research.fetch.%"))
        ).scalars()
        assert len(list(seeded)) == 5
        db.execute(delete(ScheduleDefinition).where(ScheduleDefinition.name == extra))

    command.downgrade(cfg, "0004_add_brand_topic_assignments")
    insp = inspect(engine)
    assert "research_sources" not in insp.get_table_names()
    brand_col = next(
        c for c in insp.get_columns("schedule_definitions") if c["name"] == "brand_profile_id"
    )
    assert brand_col["nullable"] is False

    command.upgrade(cfg, "head")
    ok, _, ver = check_database_health(engine)
    assert ok and ver == "0007_facebook_platform"
