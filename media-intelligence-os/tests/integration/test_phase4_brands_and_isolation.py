"""Integration tests verifying strict multi-brand isolation, seeding, and retention."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from media_os_api.db import (
    BRAND_APP_PROMO_ID,
    BRAND_TECH_AI_ID,
    Asset,
    BrandProfile,
    MetricsSnapshot,
    PublishSchedule,
    SourceItem,
    TopicCluster,
    create_db_engine,
    create_session_factory,
    seed_initial_brands,
    session_scope,
)
from media_os_api.db.retention import run_retention_evaluation
from media_os_api.services import (
    BrandIsolationError,
    BrandPolicyError,
    ClaimVerificationError,
    create_asset_scoped,
    create_brand_topic_assignment_scoped,
    create_claim_for_packet,
    create_content_package_scoped,
    create_publish_schedule_scoped,
    create_research_packet_scoped,
    create_schedule_definition_scoped,
    get_content_package_scoped,
    list_channels,
    list_content_packages_scoped,
    list_metrics_scoped,
    list_research_packets_scoped,
    reassign_content_package_brand,
    register_channel_account,
    set_brand_logo_asset_scoped,
    update_brand,
    verify_claim,
)

pytestmark = pytest.mark.integration


def _get_test_db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live brand isolation tests.")
    return url


def test_brand_seeding_idempotency_and_zero_fabricated_channels() -> None:
    engine = create_db_engine(_get_test_db_url(), pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            first = seed_initial_brands(db)
            second = seed_initial_brands(db)
            assert first.total_brands >= 2
            assert second.inserted_slugs == ()
            assert second.total_brands == first.total_brands

            tech = db.get(BrandProfile, BRAND_TECH_AI_ID)
            promo = db.get(BrandProfile, BRAND_APP_PROMO_ID)
            assert tech is not None and tech.slug == "brand-tech-ai"
            assert promo is not None and promo.slug == "brand-app-promo"
            assert promo.logo_asset_id is None
            assert promo.visual_identity.get("configured") is False
            assert len(tech.content_pillars) == 7

            # Zero channels connected by default
            assert list_channels(db, brand_ref="brand-tech-ai") == []
            assert list_channels(db, brand_ref="brand-app-promo") == []

            # Blank brand metadata updates rejected by policy
            with pytest.raises(BrandPolicyError):
                update_brand(db, brand_ref="brand-tech-ai", actor_id="owner:test", name="   ")
    finally:
        engine.dispose()


def test_cross_brand_isolation_at_service_and_database_layers() -> None:
    engine = create_db_engine(_get_test_db_url(), pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    try:
        with session_scope(factory) as db:
            tc = TopicCluster(
                normalized_title="Shared Topic Isolation",
                dedupe_key=f"iso-topic-{uuid.uuid4().hex}",
                category="AI",
                status="DISCOVERED",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(tc)
            db.flush()

            create_brand_topic_assignment_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=tc.id,
                editorial_status="APPROVED_FOR_RESEARCH",
            )

            packet_tech = create_research_packet_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=tc.id,
                editorial_angle="Tech & AI perspective",
            )
            pkg_tech = create_content_package_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                platform="INSTAGRAM",
                format_name="CAROUSEL",
                research_packet_id=packet_tech.id,
                title="Tech Carousel",
            )
            ch_tech = register_channel_account(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                platform="INSTAGRAM",
                external_account_id=f"ig-tech-{uuid.uuid4().hex[:8]}",
                postiz_integration_id=f"postiz-tech-{uuid.uuid4().hex[:8]}",
                status="CONNECTED",
            )
            ch_promo = register_channel_account(
                db,
                brand_profile_id=BRAND_APP_PROMO_ID,
                platform="INSTAGRAM",
                external_account_id=f"ig-promo-{uuid.uuid4().hex[:8]}",
                postiz_integration_id=f"postiz-promo-{uuid.uuid4().hex[:8]}",
                status="CONNECTED",
            )

            # Brand 1 queries never leak into Brand 2
            assert all(
                p.brand_profile_id == BRAND_APP_PROMO_ID
                for p in list_research_packets_scoped(db, brand_profile_id=BRAND_APP_PROMO_ID)
            )
            assert all(
                c.brand_profile_id == BRAND_APP_PROMO_ID
                for c in list_content_packages_scoped(db, brand_profile_id=BRAND_APP_PROMO_ID)
            )

            with pytest.raises(BrandIsolationError):
                get_content_package_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    content_package_id=pkg_tech.id,
                )

            # Cross-brand ResearchPacket -> ContentPackage rejected
            with pytest.raises(BrandIsolationError):
                create_content_package_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    platform="INSTAGRAM",
                    format_name="CAROUSEL",
                    research_packet_id=packet_tech.id,
                )

            # Cross-brand Asset -> ContentPackage rejected
            with pytest.raises(BrandIsolationError):
                create_asset_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    content_package_id=pkg_tech.id,
                    asset_type="CAROUSEL_SLIDE",
                    local_path="/var/lib/media-os/assets/slide.png",
                    mime_type="image/png",
                    file_size=1024,
                    checksum="a" * 64,
                )

            # Cross-brand BrandProfile.logo_asset_id rejected by service
            tech_asset = create_asset_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                content_package_id=None,
                asset_type="BRAND_LOGO",
                local_path="/var/lib/media-os/assets/tech_logo.png",
                mime_type="image/png",
                file_size=512,
                checksum="g" * 64,
            )
            with pytest.raises(BrandIsolationError):
                set_brand_logo_asset_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    logo_asset_id=tech_asset.id,
                    actor_id="owner:test",
                )

            # Cross-brand PublishSchedule rejected (Brand 2 scheduling Brand 1 content or channel)
            with pytest.raises(BrandIsolationError):
                create_publish_schedule_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    channel_account_id=ch_promo.id,
                    content_package_id=pkg_tech.id,
                    scheduled_at=now + timedelta(hours=1),
                    idempotency_key=f"bad-sched-1-{uuid.uuid4().hex}",
                )

            with pytest.raises(BrandIsolationError):
                create_publish_schedule_scoped(
                    db,
                    brand_profile_id=BRAND_TECH_AI_ID,
                    channel_account_id=ch_promo.id,
                    content_package_id=pkg_tech.id,
                    scheduled_at=now + timedelta(hours=1),
                    idempotency_key=f"bad-sched-2-{uuid.uuid4().hex}",
                )

            # Cross-brand ScheduleDefinition rejected
            with pytest.raises(BrandIsolationError):
                create_schedule_definition_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    channel_account_id=ch_tech.id,
                    name=f"bad-sched-def-{uuid.uuid4().hex[:8]}",
                    task_name="media_os.internal.ping",
                    task_type="INTERNAL_PING",
                    schedule_config={"kind": "interval", "seconds": 3600},
                    next_run_at=now + timedelta(hours=1),
                )

            # Reusing Postiz integration ID across brands rejected
            with pytest.raises(BrandIsolationError):
                register_channel_account(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    platform="INSTAGRAM",
                    external_account_id=f"ig-other-{uuid.uuid4().hex[:8]}",
                    postiz_integration_id=ch_tech.postiz_integration_id,
                )

            # Cross-brand MetricsSnapshot query rejected
            with pytest.raises(BrandIsolationError):
                list_metrics_scoped(
                    db,
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    channel_account_id=ch_tech.id,
                )

            # ContentPackage bound to MetricsSnapshot cannot be reassigned across brands
            standalone_pkg = create_content_package_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                platform="INSTAGRAM",
                format_name="CAROUSEL",
                title="Metrics Bound Package",
            )
            db.add(
                MetricsSnapshot(
                    brand_profile_id=BRAND_TECH_AI_ID,
                    channel_account_id=ch_tech.id,
                    content_package_id=standalone_pkg.id,
                    platform="INSTAGRAM",
                    metric_name="reach",
                    metric_value=Decimal("42.0000"),
                    source_api="postiz_public_v1",
                    authorization_class="AUTHORIZED_CHANNEL",
                    policy_version="v1",
                    fetched_at=now,
                    last_verified_at=now,
                    retention_expires_at=now + timedelta(days=365),
                )
            )
            db.flush()
            with pytest.raises(BrandIsolationError):
                reassign_content_package_brand(
                    db,
                    content_package_id=standalone_pkg.id,
                    new_brand_id=BRAND_APP_PROMO_ID,
                    actor_id="owner:test",
                    reason="Should reject metrics-bound package",
                )

            # Claim without ClaimSource cannot be verified at service layer either
            claim = create_claim_for_packet(
                db,
                research_packet_id=packet_tech.id,
                claim_text="Unsupported claim",
                confidence=Decimal("0.500"),
            )
            with pytest.raises(ClaimVerificationError):
                verify_claim(db, claim_id=claim.id)

            pkg_id = pkg_tech.id
            ch_promo_id = ch_promo.id

        # Prove raw PostgreSQL composite FK rejects cross-brand PublishSchedule bypassing service
        with pytest.raises(IntegrityError), session_scope(factory) as db:
            db.add(
                PublishSchedule(
                    brand_profile_id=BRAND_APP_PROMO_ID,
                    channel_account_id=ch_promo_id,
                    content_package_id=pkg_id,
                    platform="INSTAGRAM",
                    scheduled_at=now + timedelta(hours=2),
                    timezone="Asia/Kolkata",
                    status="DRAFT",
                    idempotency_key=f"raw-fk-fail-{uuid.uuid4().hex}",
                )
            )
    finally:
        engine.dispose()


def test_retention_dry_run_and_protected_asset_preservation() -> None:
    engine = create_db_engine(_get_test_db_url(), pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    past = now - timedelta(days=10)
    try:
        with session_scope(factory) as db:
            active_pkg = create_content_package_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                platform="INSTAGRAM",
                format_name="CAROUSEL",
                title="Scheduled Protected Content",
            )
            protected_temp = create_asset_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                content_package_id=active_pkg.id,
                asset_type="TEMP_INTERMEDIATE",
                local_path="/var/lib/media-os/assets/protected.png",
                mime_type="image/png",
                file_size=512,
                checksum="d" * 64,
                status="TEMP_FAILED",
                retention_expires_at=past,
            )
            deletable_temp = create_asset_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                content_package_id=None,
                asset_type="TEMP_INTERMEDIATE",
                local_path="/var/lib/media-os/assets/orphan_expired.png",
                mime_type="image/png",
                file_size=512,
                checksum="e" * 64,
                status="TEMP_FAILED",
                retention_expires_at=past,
            )
            expired_source = SourceItem(
                source_provider="GOOGLE_TRENDS",
                external_id=f"trend-exp-{uuid.uuid4().hex[:8]}",
                canonical_url=f"https://trends.google.com/{uuid.uuid4().hex}",
                title="Old Trend",
                source_label="Google Trends",
                fetched_at=now - timedelta(days=35),
                content_hash="f" * 64,
                source_policy_mode="EXPERIMENTAL",
                retention_expires_at=past,
            )
            db.add(expired_source)
            db.flush()
            protected_id = protected_temp.id
            deletable_id = deletable_temp.id
            exp_src_id = expired_source.id

        # 1. Dry-run deletes ZERO rows
        with session_scope(factory) as db:
            dry = run_retention_evaluation(db, dry_run=True, now=now)
            assert dry.dry_run is True
            assert dry.expired_temp_assets >= 1
            assert dry.protected_referenced_assets >= 1
            assert dry.deleted_temp_assets == 0
            assert db.get(Asset, deletable_id) is not None
            assert db.get(SourceItem, exp_src_id) is not None

        # 2. Live execution deletes expired unreferenced temp assets & sources, keeps protected!
        with session_scope(factory) as db:
            live = run_retention_evaluation(db, dry_run=False, now=now)
            assert live.deleted_temp_assets >= 1
            assert live.deleted_source_items >= 1
            assert db.get(Asset, deletable_id) is None
            assert db.get(SourceItem, exp_src_id) is None
            assert db.get(Asset, protected_id) is not None
    finally:
        engine.dispose()
