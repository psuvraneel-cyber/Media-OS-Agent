"""PostgreSQL tests: Alembic migrations, schema constraints, concurrency, and triggers."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from media_os_api.db import (
    BRAND_APP_PROMO_ID,
    BRAND_TECH_AI_ID,
    Asset,
    BrandProfile,
    BrandTopicAssignment,
    Claim,
    ClaimSource,
    ContentPackage,
    JobRun,
    OutboxEvent,
    ResearchPacket,
    SourceItem,
    TopicCluster,
    check_database_health,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs import enqueue_durable_job
from media_os_api.services import reassign_content_package_brand

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _get_test_db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


def _reset_schema_empty(db_url: str) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
    finally:
        engine.dispose()


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "apps" / "api" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_alembic_empty_upgrade_downgrade_and_schema_inventory() -> None:
    db_url = _get_test_db_url()
    _reset_schema_empty(db_url)
    cfg = _make_alembic_cfg(db_url)

    # 1. Stepwise upgrade from empty -> 0001 -> ... -> 0005 (head)
    command.upgrade(cfg, "0001_initial_phase4_schema")
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected_tables = {
            "alembic_version",
            "owner_users",
            "auth_sessions",
            "brand_profiles",
            "channel_accounts",
            "source_items",
            "topic_clusters",
            "topic_cluster_sources",
            "research_packets",
            "claims",
            "claim_sources",
            "content_packages",
            "content_state_transitions",
            "assets",
            "publish_schedules",
            "metrics_snapshots",
            "model_usages",
            "audit_events",
            "schedule_definitions",
            "job_runs",
            "outbox_events",
        }
        assert expected_tables.issubset(tables)

        # Verify key indexes exist
        with engine.connect() as conn:
            idx_names = {
                row[0]
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                ).fetchall()
            }
        assert {
            "ix_job_runs_due",
            "ix_job_runs_lease_expires",
            "ix_outbox_events_dispatch",
            "ix_schedule_definitions_due",
            "ix_audit_events_timestamp",
            "ix_source_items_retention_expires_at",
            "ix_metrics_snapshots_retention_expires_at",
        }.issubset(idx_names)

        # 2. Upgrade 0001 -> 0002 (seed) -> 0003 (hardening)
        command.upgrade(cfg, "0002_seed_initial_brands")
        ok2, _detail2, ver2 = check_database_health(engine)
        assert ok2 is True
        assert ver2 == "0002_seed_initial_brands"

        command.upgrade(cfg, "0003_domain_integrity_hardening")
        ok3, _detail3, ver3 = check_database_health(engine)
        assert ok3 is True
        assert ver3 == "0003_domain_integrity_hardening"

        command.upgrade(cfg, "0004_add_brand_topic_assignments")
        ok4, _detail4, ver4 = check_database_health(engine)
        assert ok4 is True
        assert ver4 == "0004_add_brand_topic_assignments"

        inspector4 = inspect(engine)
        assert "brand_topic_assignments" in set(inspector4.get_table_names())

        command.upgrade(cfg, "head")
        ok, _detail, ver = check_database_health(engine)
        assert ok is True
        assert ver == "0013_ops_requests"
        inspector5 = inspect(engine)
        assert {"research_sources", "research_source_checkpoints"}.issubset(
            set(inspector5.get_table_names())
        )

        factory = create_session_factory(engine)
        with session_scope(factory) as db:
            slugs = set(db.execute(select(BrandProfile.slug)).scalars().all())
            assert slugs == {"brand-tech-ai", "brand-app-promo"}

        # 3. Rollback 0005 -> 0004 -> 0003 -> 0002 -> 0001 and re-upgrade to head cleanly
        command.downgrade(cfg, "0004_add_brand_topic_assignments")
        ok_down4, _, ver_down4 = check_database_health(engine)
        assert ok_down4 is True and ver_down4 == "0004_add_brand_topic_assignments"
        assert "research_sources" not in set(inspect(engine).get_table_names())

        command.downgrade(cfg, "0003_domain_integrity_hardening")
        ok_down3, _, ver_down3 = check_database_health(engine)
        assert ok_down3 is True and ver_down3 == "0003_domain_integrity_hardening"

        command.downgrade(cfg, "0002_seed_initial_brands")
        ok_down2, _, ver_down2 = check_database_health(engine)
        assert ok_down2 is True and ver_down2 == "0002_seed_initial_brands"

        command.downgrade(cfg, "0001_initial_phase4_schema")
        with session_scope(factory) as db:
            assert db.execute(select(BrandProfile.id)).scalars().all() == []
        command.upgrade(cfg, "head")
        with session_scope(factory) as db:
            assert len(db.execute(select(BrandProfile.id)).scalars().all()) == 2
    finally:
        engine.dispose()


def test_unique_constraints_triggers_rollback_and_connection_failure() -> None:
    db_url = _get_test_db_url()
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    try:
        # Transaction rollback leaves DB clean
        with pytest.raises(RuntimeError, match="abort-tx"), session_scope(factory) as db:
            db.add(
                SourceItem(
                    source_provider="HACKER_NEWS",
                    external_id="rollback-test-1",
                    canonical_url="https://example.com/rollback",
                    title="Rollback Test",
                    source_label="HN",
                    fetched_at=now,
                    content_hash="a" * 64,
                    source_policy_mode="ENABLED",
                    retention_expires_at=now + timedelta(days=365),
                )
            )
            raise RuntimeError("abort-tx")

        with session_scope(factory) as db:
            found = db.execute(
                select(SourceItem).where(SourceItem.external_id == "rollback-test-1")
            ).scalar_one_or_none()
            assert found is None

        # Unique constraint on (source_provider, external_id)
        with session_scope(factory) as db:
            s1 = SourceItem(
                source_provider="GITHUB",
                external_id="repo-123",
                canonical_url="https://github.com/org/repo",
                title="Repo 123",
                source_label="GitHub",
                fetched_at=now,
                content_hash="b" * 64,
                source_policy_mode="ENABLED",
                retention_expires_at=now + timedelta(days=365),
            )
            db.add(s1)
            db.flush()
            source_id = s1.id

        with pytest.raises(IntegrityError), session_scope(factory) as db:
            db.add(
                SourceItem(
                    source_provider="GITHUB",
                    external_id="repo-123",
                    canonical_url="https://github.com/org/repo-dup",
                    title="Repo 123 Dup",
                    source_label="GitHub",
                    fetched_at=now,
                    content_hash="c" * 64,
                    source_policy_mode="ENABLED",
                    retention_expires_at=now + timedelta(days=365),
                )
            )

        # Claim cannot be marked VERIFIED without ClaimSource AND cannot lose last ClaimSource
        with session_scope(factory) as db:
            tc = TopicCluster(
                normalized_title="Trigger Test Topic",
                dedupe_key=f"trigger-{uuid.uuid4().hex}",
                category="AI",
                status="DISCOVERED",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(tc)
            db.flush()
            bta = BrandTopicAssignment(
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=tc.id,
                editorial_status="APPROVED_FOR_RESEARCH",
            )
            db.add(bta)
            db.flush()
            rp = ResearchPacket(
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=tc.id,
                editorial_angle="Technical verification",
            )
            db.add(rp)
            db.flush()
            claim = Claim(
                research_packet_id=rp.id,
                claim_text="Release supports JSON schema.",
                confidence=Decimal("0.900"),
                verification_status="UNVERIFIED",
            )
            db.add(claim)
            db.flush()
            claim_id = claim.id

        with pytest.raises(IntegrityError), session_scope(factory) as db:
            c = db.get(Claim, claim_id)
            assert c is not None
            c.verification_status = "VERIFIED"

        with session_scope(factory) as db:
            db.add(ClaimSource(claim_id=claim_id, source_item_id=source_id))
            db.flush()
            c = db.get(Claim, claim_id)
            assert c is not None
            c.verification_status = "VERIFIED"

        # Deleting the last ClaimSource from a VERIFIED Claim is blocked by DB trigger!
        with pytest.raises(IntegrityError), session_scope(factory) as db:
            db.execute(delete(ClaimSource).where(ClaimSource.claim_id == claim_id))

        # Deleting the Claim itself still cascades cleanly without trigger block
        with session_scope(factory) as db:
            c_del = db.get(Claim, claim_id)
            assert c_del is not None
            db.delete(c_del)

        # Invalid ContentPackage workflow status rejected at PostgreSQL constraint level
        with pytest.raises(IntegrityError), session_scope(factory) as db:
            db.add(
                ContentPackage(
                    brand_profile_id=BRAND_TECH_AI_ID,
                    platform="INSTAGRAM",
                    format="CAROUSEL",
                    status="ARBITRARY_BOGUS_STATE",
                    title="Invalid Status Test",
                )
            )

        # Cross-brand BrandProfile.logo_asset_id rejected by DB trigger
        with session_scope(factory) as db:
            promo_asset = Asset(
                brand_profile_id=BRAND_APP_PROMO_ID,
                asset_type="BRAND_LOGO",
                local_path="/var/lib/media-os/assets/promo_logo.png",
                mime_type="image/png",
                file_size=256,
                checksum="d" * 64,
                status="ACTIVE",
            )
            db.add(promo_asset)
            db.flush()
            promo_asset_id = promo_asset.id

        with pytest.raises(IntegrityError), session_scope(factory) as db:
            tech_brand = db.get(BrandProfile, BRAND_TECH_AI_ID)
            assert tech_brand is not None
            tech_brand.logo_asset_id = promo_asset_id

        # ContentPackage brand_profile_id immutability trigger + audited reassignment
        with session_scope(factory) as db:
            pkg = ContentPackage(
                brand_profile_id=BRAND_TECH_AI_ID,
                platform="INSTAGRAM",
                format="CAROUSEL",
                status="DRAFTED",
                title="Immutable Brand Test",
            )
            db.add(pkg)
            db.flush()
            pkg_id = pkg.id

        with pytest.raises(IntegrityError), session_scope(factory) as db:
            p = db.get(ContentPackage, pkg_id)
            assert p is not None
            p.brand_profile_id = BRAND_APP_PROMO_ID

        with session_scope(factory) as db:
            reassigned = reassign_content_package_brand(
                db,
                content_package_id=pkg_id,
                new_brand_id=BRAND_APP_PROMO_ID,
                actor_id="owner:test",
                reason="Explicit owner brand reassignment test",
            )
            assert reassigned.brand_profile_id == BRAND_APP_PROMO_ID

        # Unreachable DB fails safely without leaking credentials
        bad_engine = create_db_engine(
            "postgresql+psycopg://baduser:VerySecretDbPass@127.0.0.1:59999/nodb",
            connect_timeout_seconds=1,
        )
        try:
            ok, msg, schema_ver = check_database_health(bad_engine)
            assert ok is False
            assert schema_ver is None
            assert "VerySecretDbPass" not in msg
        finally:
            bad_engine.dispose()
    finally:
        engine.dispose()


def test_concurrent_job_enqueue_deduplication_in_postgres() -> None:
    db_url = _get_test_db_url()
    engine = create_db_engine(db_url, pool_size=6, max_overflow=2)
    factory = create_session_factory(engine)
    shared_key = f"concurrent-key-{uuid.uuid4().hex}"
    results: list[tuple[uuid.UUID, bool]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def _worker() -> None:
        barrier.wait(timeout=10)
        with session_scope(factory) as db:
            job, created = enqueue_durable_job(
                db,
                task_name="media_os.internal.ping",
                task_type="INTERNAL_PING",
                payload={"echo": "race"},
                idempotency_key=shared_key,
            )
            with lock:
                results.append((job.id, created))

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    try:
        assert len(results) == 4
        ids = {job_id for job_id, _ in results}
        created_flags = [flag for _, flag in results]
        assert len(ids) == 1
        assert created_flags.count(True) == 1
        assert created_flags.count(False) == 3

        with session_scope(factory) as db:
            job_rows = (
                db.execute(select(JobRun).where(JobRun.idempotency_key == shared_key))
                .scalars()
                .all()
            )
            outbox_rows = (
                db.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_rows[0].id))
                .scalars()
                .all()
            )
            assert len(job_rows) == 1
            assert len(outbox_rows) == 1
    finally:
        engine.dispose()
