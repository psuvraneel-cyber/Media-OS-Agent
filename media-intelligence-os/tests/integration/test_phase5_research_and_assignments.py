"""Phase 5 Acceptance Tests: Shared Research Ingestion & BrandTopicAssignment Invariants.

Enforces all 8 requirements of the Mandatory Phase 5 Architectural Addendum:
1. One SourceItem can support multiple brands.
2. One TopicCluster can support multiple brands.
3. Each active brand receives exactly one assignment upon discovery.
4. Repeated ingestion creates no duplicate assignments (idempotency).
5. Unevaluated assignments have relevance_score = NULL.
6. Brand-specific rejection does not affect another brand.
7. A ResearchPacket cannot be created from a rejected or pending assignment.
8. Worker retries do not create duplicate source, topic, or assignment records.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from media_os_api.db import (
    BRAND_APP_PROMO_ID,
    BRAND_TECH_AI_ID,
    BrandProfile,
    BrandTopicAssignment,
    SourceItem,
    TopicCluster,
    TopicClusterSource,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.services.brand_service import (
    BrandPolicyError,
    create_research_packet_scoped,
    update_brand_topic_assignment_scoped,
)
from media_os_api.services.research_service import (
    ingest_discovery_candidate,
)

pytestmark = pytest.mark.integration


def _get_test_db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


def test_shared_research_ingestion_and_multi_brand_assignment_lifecycle() -> None:
    """Acceptance Tests 1-6: Ingestion, multi-brand assignment, NULL score, and editorial
    isolation.
    """
    db_url = _get_test_db_url()
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)

    raw_paper_id = f"2403.{uuid.uuid4().hex[:5]}v1"
    candidate = RawDiscoveryCandidate(
        source_provider=ResourceNamespace.ARXIV_PAPER,
        external_id=raw_paper_id,
        raw_url=f"https://arxiv.org/abs/{raw_paper_id}",
        title=f"Scalable Neural Representations {raw_paper_id}",
        source_label="arXiv Paper",
        category="AI Research",
        published_at=datetime.now(UTC),
        summary="<p>An abstract on scalable neural models.</p>",
    )

    try:
        with session_scope(factory) as db:
            # Verify active seeded brands exist
            active_brands = (
                db.execute(select(BrandProfile).where(BrandProfile.is_active.is_(True)))
                .scalars()
                .all()
            )
            assert len(active_brands) >= 2
            active_brand_ids = {b.id for b in active_brands}
            assert BRAND_TECH_AI_ID in active_brand_ids
            assert BRAND_APP_PROMO_ID in active_brand_ids

            # Initial ingestion
            result = ingest_discovery_candidate(db, candidate)
            assert result.is_new_source is True
            assert result.is_new_cluster is True
            assert result.assignments_created == len(active_brands)

            # 1. One SourceItem supports multiple brands
            source_item = db.get(SourceItem, result.source_item_id)
            assert source_item is not None
            assert source_item.minimal_permitted_summary == "An abstract on scalable neural models."
            assert source_item.source_policy_mode == "EXPERIMENTAL"

            # 2. One TopicCluster supports multiple brands
            topic_cluster = db.get(TopicCluster, result.topic_cluster_id)
            assert topic_cluster is not None
            assert topic_cluster.status == "DISCOVERED"
            assert topic_cluster.score_breakdown == {}

            # 3. Each active brand receives exactly one assignment
            assignments = (
                db.execute(
                    select(BrandTopicAssignment).where(
                        BrandTopicAssignment.topic_cluster_id == topic_cluster.id
                    )
                )
                .scalars()
                .all()
            )
            assert len(assignments) == len(active_brands)
            assignment_brand_ids = {a.brand_profile_id for a in assignments}
            assert assignment_brand_ids == active_brand_ids

            # 5. Unevaluated assignments have NULL scores and PENDING_EVALUATION status
            for a in assignments:
                assert a.editorial_status == "PENDING_EVALUATION"
                assert a.relevance_score is None

            # 4. Repeated ingestion creates NO duplicate assignments (Idempotency)
            retry_result = ingest_discovery_candidate(db, candidate)
            assert retry_result.is_new_source is False
            assert retry_result.is_new_cluster is False
            assert retry_result.assignments_created == 0

            assignments_after_retry = (
                db.execute(
                    select(BrandTopicAssignment).where(
                        BrandTopicAssignment.topic_cluster_id == topic_cluster.id
                    )
                )
                .scalars()
                .all()
            )
            assert len(assignments_after_retry) == len(active_brands)

            # 6. Brand-specific rejection does not affect another brand
            # Reject for Brand 1 (Technology & AI)
            update_brand_topic_assignment_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=topic_cluster.id,
                editorial_status="REJECTED",
                rejection_reason="Duplicate niche coverage.",
            )

            # Shortlist for Brand 2 (App Promotion)
            update_brand_topic_assignment_scoped(
                db,
                brand_profile_id=BRAND_APP_PROMO_ID,
                topic_cluster_id=topic_cluster.id,
                editorial_status="SHORTLISTED",
                relevance_score=Decimal("0.8500"),
                score_breakdown={"relevance": 0.85, "freshness": 0.9},
            )

            # Inspect isolated states
            assign_brand1 = (
                db.execute(
                    select(BrandTopicAssignment).where(
                        BrandTopicAssignment.brand_profile_id == BRAND_TECH_AI_ID,
                        BrandTopicAssignment.topic_cluster_id == topic_cluster.id,
                    )
                )
                .scalars()
                .one()
            )
            assign_brand2 = (
                db.execute(
                    select(BrandTopicAssignment).where(
                        BrandTopicAssignment.brand_profile_id == BRAND_APP_PROMO_ID,
                        BrandTopicAssignment.topic_cluster_id == topic_cluster.id,
                    )
                )
                .scalars()
                .one()
            )

            assert assign_brand1.editorial_status == "REJECTED"
            assert assign_brand1.rejection_reason == "Duplicate niche coverage."
            assert assign_brand1.relevance_score is None

            assert assign_brand2.editorial_status == "SHORTLISTED"
            assert assign_brand2.relevance_score == Decimal("0.8500")
            assert assign_brand2.score_breakdown.get("relevance") == 0.85

            # Global cluster status remains unpolluted
            cluster_refresh = db.get(TopicCluster, topic_cluster.id)
            assert cluster_refresh is not None
            assert cluster_refresh.status == "DISCOVERED"
            assert cluster_refresh.score_breakdown == {}

    finally:
        engine.dispose()


def test_research_packet_creation_gated_by_assignment_status() -> None:
    """Acceptance Test 7: A ResearchPacket cannot be created from a rejected or pending
    assignment.
    """
    db_url = _get_test_db_url()
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)

    candidate = RawDiscoveryCandidate(
        source_provider=ResourceNamespace.GITHUB_REPOSITORY,
        external_id=f"test-org/gating-repo-{uuid.uuid4().hex[:6]}",
        raw_url=f"https://github.com/test-org/gating-repo-{uuid.uuid4().hex[:6]}",
        title="Gating Test Repository",
        source_label="GitHub Repository",
        category="Developer Tools",
        published_at=datetime.now(UTC),
    )

    try:
        with session_scope(factory) as db:
            result = ingest_discovery_candidate(db, candidate)

            # Case A: Assignment is PENDING_EVALUATION -> Creation rejected
            with pytest.raises(BrandPolicyError, match="not eligible for research"):
                create_research_packet_scoped(
                    db,
                    brand_profile_id=BRAND_TECH_AI_ID,
                    topic_cluster_id=result.topic_cluster_id,
                    editorial_angle="Pending test angle",
                )

            # Case B: Assignment is REJECTED -> Creation rejected
            update_brand_topic_assignment_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=result.topic_cluster_id,
                editorial_status="REJECTED",
                rejection_reason="Not applicable to brand scope.",
            )

            with pytest.raises(BrandPolicyError, match="not eligible for research"):
                create_research_packet_scoped(
                    db,
                    brand_profile_id=BRAND_TECH_AI_ID,
                    topic_cluster_id=result.topic_cluster_id,
                    editorial_angle="Rejected test angle",
                )

            # Case C: Assignment is APPROVED_FOR_RESEARCH -> Creation succeeds
            update_brand_topic_assignment_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=result.topic_cluster_id,
                editorial_status="APPROVED_FOR_RESEARCH",
            )

            packet = create_research_packet_scoped(
                db,
                brand_profile_id=BRAND_TECH_AI_ID,
                topic_cluster_id=result.topic_cluster_id,
                editorial_angle="Approved deep dive angle",
                key_findings=["Finding 1", "Finding 2"],
            )
            assert packet is not None
            assert packet.brand_profile_id == BRAND_TECH_AI_ID
            assert packet.topic_cluster_id == result.topic_cluster_id
            assert packet.status == "DRAFT"

    finally:
        engine.dispose()


def test_worker_retry_idempotency_creates_zero_duplicates() -> None:
    """Acceptance Test 8: Worker retries do not create duplicate source, topic or
    assignment records.
    """
    db_url = _get_test_db_url()
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)

    external_id = f"test-worker-repo-{uuid.uuid4().hex[:6]}"
    candidate = RawDiscoveryCandidate(
        source_provider=ResourceNamespace.GITHUB_REPOSITORY,
        external_id=external_id,
        raw_url=f"https://github.com/media-test/{external_id}",
        title="Worker Retry Test Repository",
        source_label="GitHub Repository",
        category="Developer Tools",
        published_at=datetime.now(UTC),
    )

    try:
        with session_scope(factory) as db:
            # Simulate 3 worker retry runs
            res1 = ingest_discovery_candidate(db, candidate)
            res2 = ingest_discovery_candidate(db, candidate)
            res3 = ingest_discovery_candidate(db, candidate)

            assert res1.is_new_source is True
            assert res1.is_new_cluster is True

            assert res2.is_new_source is False
            assert res2.is_new_cluster is False
            assert res2.assignments_created == 0

            assert res3.is_new_source is False
            assert res3.is_new_cluster is False
            assert res3.assignments_created == 0

            # Verify exact record counts in DB
            sources = (
                db.execute(
                    select(SourceItem).where(
                        SourceItem.source_provider == ResourceNamespace.GITHUB_REPOSITORY,
                        SourceItem.external_id == external_id,
                    )
                )
                .scalars()
                .all()
            )
            assert len(sources) == 1

            clusters = (
                db.execute(select(TopicCluster).where(TopicCluster.id == res1.topic_cluster_id))
                .scalars()
                .all()
            )
            assert len(clusters) == 1

            provenance_links = (
                db.execute(
                    select(TopicClusterSource).where(
                        TopicClusterSource.topic_cluster_id == res1.topic_cluster_id,
                        TopicClusterSource.source_item_id == res1.source_item_id,
                    )
                )
                .scalars()
                .all()
            )
            assert len(provenance_links) == 1

            assignments = (
                db.execute(
                    select(BrandTopicAssignment).where(
                        BrandTopicAssignment.topic_cluster_id == res1.topic_cluster_id
                    )
                )
                .scalars()
                .all()
            )
            # Exactly one assignment per active brand
            active_brand_count = (
                db.execute(select(BrandProfile).where(BrandProfile.is_active.is_(True)))
                .scalars()
                .all()
            )
            assert len(assignments) == len(active_brand_count)

    finally:
        engine.dispose()
