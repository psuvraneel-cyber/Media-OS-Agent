"""Integration tests for durable jobs, outbox crash recovery, scheduler locking, and workflow."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db import (
    BRAND_TECH_AI_ID,
    ContentStateTransition,
    JobRun,
    OutboxEvent,
    ScheduleDefinition,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs import (
    JobIdempotencyConflictError,
    PermanentTaskError,
    RetryableTaskError,
    claim_job_run_for_execution,
    claim_outbox_batch,
    dispatch_outbox_batch,
    enqueue_durable_job,
    execute_durable_job,
    recover_stale_leases,
    run_scheduler_tick,
)
from media_os_api.services import create_content_package_scoped, create_schedule_definition_scoped
from media_os_api.workflow import (
    ContentLifecycleStatus,
    InvalidStateTransitionError,
    TransitionIdempotencyConflictError,
    transition_content_status,
)

pytestmark = pytest.mark.integration


def _get_test_db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping durable jobs/outbox tests.")
    return url


def test_outbox_crash_recovery_retry_backoff_permanent_failure_and_duplicate_skip() -> None:
    engine = create_db_engine(_get_test_db_url(), pool_size=6, max_overflow=2)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)

    class _FakePublisher:
        def __init__(self, *, fail_first: int = 0) -> None:
            self.fail_first = fail_first
            self.published: list[uuid.UUID] = []

        def publish_job(
            self,
            *,
            job_run_id: uuid.UUID,
            task_name: str,
            queue_name: str,
            attempt: int,
            request_id: str,
        ) -> str:
            if self.fail_first > 0:
                self.fail_first -= 1
                raise ConnectionError("Broker temporarily unreachable")
            self.published.append(job_run_id)
            return f"fake-{job_run_id}-{attempt}"

    try:
        # 1. Duplicate idempotency conflict detection
        idem_key = f"job-idem-{uuid.uuid4().hex}"
        with session_scope(factory) as db:
            j1, created1 = enqueue_durable_job(
                db,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                payload={"echo": "v1"},
                idempotency_key=idem_key,
                available_at=now,
            )
            assert created1 is True
            j2, created2 = enqueue_durable_job(
                db,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                payload={"echo": "v1"},
                idempotency_key=idem_key,
                available_at=now,
            )
            assert created2 is False
            assert j2.id == j1.id

            with pytest.raises(JobIdempotencyConflictError):
                enqueue_durable_job(
                    db,
                    task_name="media_os.internal.ping",
                    task_type="INTERNAL_PING",
                    payload={"echo": "DIFFERENT"},
                    idempotency_key=idem_key,
                )
            job1_id = j1.id

        # 2. Dispatcher crash recovery: claim -> crash -> lease expiry -> recover
        with session_scope(factory) as db:
            claimed = claim_outbox_batch(db, claim_lease_seconds=0, now=now)
            assert any(item.job_run_id == job1_id for item in claimed)

        pub_ok = _FakePublisher()
        # Because claim_lease_seconds=0, lease is already expired at now + 1s!
        stats_recovered = dispatch_outbox_batch(
            factory,
            publisher=pub_ok,
            now=now + timedelta(seconds=1),
        )
        assert stats_recovered.published >= 1
        assert job1_id in pub_ok.published

        # 3. Duplicate queue delivery executes logical handler ONCE
        run_counter = 0

        def _counting_handler(_session: Session, job: JobRun) -> dict[str, Any]:
            nonlocal run_counter
            run_counter += 1
            return {"count": run_counter, "id": str(job.id)}

        registry = {"media_os.internal.ping": _counting_handler}
        res_a = execute_durable_job(factory, job_run_id=job1_id, registry=registry)
        res_b = execute_durable_job(factory, job_run_id=job1_id, registry=registry)
        assert res_a["status"] == "SUCCEEDED" and res_a["duplicate_skipped"] is False
        assert res_b["status"] == "SUCCEEDED" and res_b["duplicate_skipped"] is True
        assert run_counter == 1

        # 4. Outbox retry on broker outage
        outbox_fail_key = f"outbox-retry-{uuid.uuid4().hex}"
        with session_scope(factory) as db:
            j_ob, _ = enqueue_durable_job(
                db,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                payload={"echo": "outbox-retry"},
                idempotency_key=outbox_fail_key,
                available_at=now,
            )
            j_ob_id = j_ob.id

        flaky_pub = _FakePublisher(fail_first=1)
        st_fail = dispatch_outbox_batch(factory, publisher=flaky_pub, now=now)
        assert st_fail.retried >= 1
        with session_scope(factory) as db:
            ob_row = db.execute(
                select(OutboxEvent).where(OutboxEvent.job_run_id == j_ob_id)
            ).scalar_one()
            assert ob_row.status == "FAILED_RETRYABLE"
            retry_time = ob_row.next_attempt_at
        st_ok = dispatch_outbox_batch(
            factory,
            publisher=flaky_pub,
            now=retry_time + timedelta(seconds=1),
        )
        assert st_ok.published >= 1

        # 5. Retryable failure -> bounded retries -> eventual success OR max-attempts FAILED_FINAL
        retry_key = f"worker-retry-{uuid.uuid4().hex}"
        with session_scope(factory) as db:
            j_retry, _ = enqueue_durable_job(
                db,
                task_name="test.flaky",
                task_type="TEST_FLAKY",
                payload={"n": 1},
                idempotency_key=retry_key,
                maximum_attempts=2,
            )
            j_retry_id = j_retry.id

        def _always_transient(_s: Session, _j: JobRun) -> dict[str, Any]:
            raise RetryableTaskError("Temporary upstream 503", code="UPSTREAM_503")

        r1 = execute_durable_job(
            factory,
            job_run_id=j_retry_id,
            registry={"test.flaky": _always_transient},
            base_backoff_seconds=1.0,
        )
        assert r1["status"] == "FAILED_RETRYABLE"
        r2 = execute_durable_job(
            factory,
            job_run_id=j_retry_id,
            registry={"test.flaky": _always_transient},
            base_backoff_seconds=1.0,
        )
        assert r2["status"] == "FAILED_FINAL"

        # 6. Permanent failure stops on attempt 1 without retries
        perm_key = f"worker-perm-{uuid.uuid4().hex}"
        with session_scope(factory) as db:
            j_perm, _ = enqueue_durable_job(
                db,
                task_name="test.perm",
                task_type="TEST_PERM",
                payload={"n": 1},
                idempotency_key=perm_key,
                maximum_attempts=3,
            )
            j_perm_id = j_perm.id

        def _permanent_fail(_s: Session, _j: JobRun) -> dict[str, Any]:
            raise PermanentTaskError("Invalid schema", code="BAD_SCHEMA")

        rp = execute_durable_job(
            factory,
            job_run_id=j_perm_id,
            registry={"test.perm": _permanent_fail},
        )
        assert rp["status"] == "FAILED_FINAL"
        with session_scope(factory) as db:
            perm_row = db.get(JobRun, j_perm_id)
            assert perm_row is not None and perm_row.attempt_count == 1

        # 7. Worker crash & lease expiration recovery
        crash_key = f"worker-crash-{uuid.uuid4().hex}"
        with session_scope(factory) as db:
            j_crash, _ = enqueue_durable_job(
                db,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                payload={"echo": "crash-recover"},
                idempotency_key=crash_key,
                maximum_attempts=2,
            )
            j_crash_id = j_crash.id

        with session_scope(factory) as db:
            claim = claim_job_run_for_execution(
                db,
                job_run_id=j_crash_id,
                lease_seconds=5,
                now=now,
            )
            assert claim.should_execute is True

        # Before lease expires: active lease prevents another worker from running twice
        with session_scope(factory) as db:
            concurrent_claim = claim_job_run_for_execution(
                db,
                job_run_id=j_crash_id,
                lease_seconds=5,
                now=now + timedelta(seconds=2),
            )
            assert concurrent_claim.leased_elsewhere is True

        # After lease expires: scheduler recovers stale RUNNING job to FAILED_RETRYABLE + PENDING
        recovered = recover_stale_leases(factory, now=now + timedelta(seconds=10))
        assert recovered >= 1
        with session_scope(factory) as db:
            crashed = db.get(JobRun, j_crash_id)
            assert crashed is not None and crashed.status == "FAILED_RETRYABLE"
            assert crashed.last_error_code == "WORKER_LEASE_EXPIRED"

        # 8. Outbox post-publish race guard: fast worker RetryableTaskError before dispatcher
        # second commit MUST NOT overwrite OutboxEvent back to PUBLISHED!
        race_key = f"outbox-fast-race-{uuid.uuid4().hex}"
        with session_scope(factory) as db:
            j_race, _ = enqueue_durable_job(
                db,
                task_name="test.fast.race",
                task_type="TEST_RACE",
                payload={"race": True},
                idempotency_key=race_key,
                maximum_attempts=3,
                available_at=now,
            )
            j_race_id = j_race.id

        class _FastFailingWorkerPublisher:
            def publish_job(
                self,
                *,
                job_run_id: uuid.UUID,
                task_name: str,
                queue_name: str,
                attempt: int,
                request_id: str,
            ) -> str:
                if job_run_id == j_race_id:
                    execute_durable_job(
                        factory,
                        job_run_id=j_race_id,
                        registry={"test.fast.race": _always_transient},
                        base_backoff_seconds=1.0,
                    )
                return f"race-{job_run_id}-{attempt}"

        dispatch_outbox_batch(factory, publisher=_FastFailingWorkerPublisher(), now=now)
        with session_scope(factory) as db:
            race_ob = db.execute(
                select(OutboxEvent).where(OutboxEvent.job_run_id == j_race_id)
            ).scalar_one()
            race_job = db.get(JobRun, j_race_id)
            assert race_job is not None and race_job.status == "FAILED_RETRYABLE"
            assert race_ob.status == "PENDING"
            assert race_ob.attempt_count == 0
    finally:
        engine.dispose()


def test_real_celery_worker_executes_outbox_json_task_end_to_end() -> None:
    import subprocess
    import time

    db_url = _get_test_db_url()
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    if not redis_url:
        pytest.skip("MEDIA_OS_TEST_REDIS_URL not set; skipping real Celery worker E2E test.")

    from media_os_api.auth import hash_password
    from media_os_api.config import load_settings
    from media_os_api.jobs import CeleryTaskPublisher, build_celery_app

    settings = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": "celery-e2e-owner",
            "OWNER_PASSWORD_HASH": hash_password("Phase4-Celery-Test-Pass!2026"),
            "SESSION_SECRET": "phase4-celery-e2e-secret-0123456789abcdef0123456789abcdef",
            "DATABASE_URL": db_url,
            "REDIS_URL": redis_url,
            "CELERY_BROKER_URL": redis_url,
            "CELERY_RESULT_BACKEND": redis_url,
        }
    )
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    celery_app = build_celery_app(settings)
    publisher = CeleryTaskPublisher(celery_app)

    env = dict(os.environ)
    env["APP_ENV"] = "test"
    env["OWNER_USERNAME"] = "celery-e2e-owner"
    env["OWNER_PASSWORD_HASH"] = settings.resolved_owner_password_hash()
    env["SESSION_SECRET"] = settings.session_secret.get_secret_value()
    env["DATABASE_URL"] = db_url
    env["REDIS_URL"] = redis_url
    env["CELERY_BROKER_URL"] = redis_url
    env["CELERY_RESULT_BACKEND"] = redis_url
    env["C_FORCE_ROOT"] = "1"

    worker_proc = subprocess.Popen(
        [
            "celery",
            "-A",
            "media_os_api.jobs.celery_app:celery_app",
            "worker",
            "--loglevel=WARNING",
            "--concurrency=1",
            "-Q",
            "control,maintenance,default",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        idem_key = f"real-celery-e2e-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        with session_scope(factory) as db:
            job, created = enqueue_durable_job(
                db,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                payload={"echo": "celery-json-e2e"},
                idempotency_key=idem_key,
                brand_profile_id=BRAND_TECH_AI_ID,
                available_at=now,
            )
            assert created is True
            job_id = job.id

        stats = dispatch_outbox_batch(factory, publisher=publisher, now=now)
        assert stats.published >= 1

        # Wait up to 25s for the real Celery worker process to execute and persist SUCCEEDED
        succeeded = False
        for _ in range(50):
            with session_scope(factory) as db:
                row = db.get(JobRun, job_id)
                if row is not None and row.status == "SUCCEEDED":
                    assert row.result_payload is not None
                    assert row.result_payload.get("echo") == "celery-json-e2e"
                    assert row.attempt_count == 1
                    succeeded = True
                    break
            time.sleep(0.5)
        assert succeeded, f"Real Celery worker did not complete JobRun {job_id} in time."
    finally:
        worker_proc.terminate()
        try:
            worker_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker_proc.kill()
        engine.dispose()


def test_persistent_scheduler_concurrent_ticks_create_one_logical_job() -> None:
    engine = create_db_engine(_get_test_db_url(), pool_size=6, max_overflow=2)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    sched_name = f"concurrent-sched-{uuid.uuid4().hex[:10]}"
    bad_sched_name = f"invalid-sched-{uuid.uuid4().hex[:10]}"
    try:
        with session_scope(factory) as db:
            sched = create_schedule_definition_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                name=sched_name,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                schedule_config={"kind": "interval", "seconds": 3600},
                next_run_at=now - timedelta(seconds=5),
                timezone="Asia/Kolkata",
                is_enabled=True,
            )
            bad_sched = create_schedule_definition_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                name=bad_sched_name,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                schedule_config={"kind": "unsupported_kind"},
                next_run_at=now - timedelta(seconds=10),
                timezone="Asia/Kolkata",
                is_enabled=True,
            )
            sched_id = sched.id
            bad_sched_id = bad_sched.id

        barrier = threading.Barrier(3)
        results: list[int] = []
        lock = threading.Lock()

        def _tick() -> None:
            barrier.wait(timeout=10)
            tick_res = run_scheduler_tick(factory, now=now)
            with lock:
                results.append(tick_res.jobs_created)

        threads = [threading.Thread(target=_tick) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert sum(results) == 1
        # Duplicate tick at same timestamp creates zero additional jobs
        dup_tick = run_scheduler_tick(factory, now=now)
        assert dup_tick.jobs_created == 0

        with session_scope(factory) as db:
            jobs = (
                db.execute(select(JobRun).where(JobRun.schedule_definition_id == sched_id))
                .scalars()
                .all()
            )
            assert len(jobs) == 1
            sched_row = db.get(ScheduleDefinition, sched_id)
            assert sched_row is not None
            sched_row.is_enabled = False
            bad_row = db.get(ScheduleDefinition, bad_sched_id)
            assert bad_row is not None and bad_row.is_enabled is False
    finally:
        engine.dispose()


def test_content_workflow_state_machine_db_transitions_and_guards() -> None:
    engine = create_db_engine(_get_test_db_url(), pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            pkg = create_content_package_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                platform="INSTAGRAM",
                format_name="CAROUSEL",
                title="State Machine Lifecycle Test",
            )
            pkg_id = pkg.id

        # Legal stepwise advancement with idempotency
        steps = [
            ContentLifecycleStatus.NORMALIZED,
            ContentLifecycleStatus.DEDUPED,
            ContentLifecycleStatus.RANKED,
            ContentLifecycleStatus.RESEARCHED,
            ContentLifecycleStatus.DRAFTED,
        ]
        for step in steps:
            with session_scope(factory) as db:
                res = transition_content_status(
                    db,
                    content_package_id=pkg_id,
                    to_status=step,
                    actor_id="owner:test",
                    idempotency_key=f"step-{step.value}",
                )
                assert res.idempotent_replay is False

        # Replaying same transition with same idempotency key is idempotent
        with session_scope(factory) as db:
            replay = transition_content_status(
                db,
                content_package_id=pkg_id,
                to_status=ContentLifecycleStatus.DRAFTED,
                actor_id="owner:test",
                idempotency_key="step-DRAFTED",
            )
            assert replay.idempotent_replay is True

        # Reusing idempotency key for DIFFERENT status fails
        with pytest.raises(TransitionIdempotencyConflictError), session_scope(factory) as db:
            transition_content_status(
                db,
                content_package_id=pkg_id,
                to_status=ContentLifecycleStatus.RENDERED,
                actor_id="owner:test",
                idempotency_key="step-DRAFTED",
            )

        # Illegal skip DRAFTED -> PUBLISHED rejected
        with pytest.raises(InvalidStateTransitionError), session_scope(factory) as db:
            transition_content_status(
                db,
                content_package_id=pkg_id,
                to_status=ContentLifecycleStatus.PUBLISHED,
                actor_id="owner:test",
            )

        # Pause from DRAFTED, reject resume to PUBLISHED, allow resume to DRAFTED
        with session_scope(factory) as db:
            transition_content_status(
                db,
                content_package_id=pkg_id,
                to_status=ContentLifecycleStatus.PAUSED,
                actor_id="owner:test",
                reason="Manual pause",
            )
        with pytest.raises(InvalidStateTransitionError), session_scope(factory) as db:
            transition_content_status(
                db,
                content_package_id=pkg_id,
                to_status=ContentLifecycleStatus.PUBLISHED,
                actor_id="owner:test",
            )
        with session_scope(factory) as db:
            resumed = transition_content_status(
                db,
                content_package_id=pkg_id,
                to_status=ContentLifecycleStatus.DRAFTED,
                actor_id="owner:test",
                reason="Resume draft",
            )
            assert resumed.to_status == ContentLifecycleStatus.DRAFTED

            history = (
                db.execute(
                    select(ContentStateTransition).where(
                        ContentStateTransition.content_package_id == pkg_id
                    )
                )
                .scalars()
                .all()
            )
            # Initial + 5 steps + PAUSED + DRAFTED resume = 8 transition records
            assert len(history) == 8
    finally:
        engine.dispose()
