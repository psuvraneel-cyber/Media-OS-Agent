"""Research candidate ingestion service implementing Phase 5 architectural invariants."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db.models import (
    BrandProfile,
    BrandTopicAssignment,
    SourceItem,
    TopicCluster,
    TopicClusterSource,
)
from media_os_api.db.normalization import compute_content_hash, normalize_canonical_url
from media_os_api.research.models import (
    IngestionResult,
    PolicyBlockedError,
    RawDiscoveryCandidate,
    ResourceNamespaceError,
)
from media_os_api.research.namespaces import (
    AUTHORIZED_NAMESPACES,
    BLOCKED_NAMESPACES,
    SourcePolicyMode,
)
from media_os_api.research.sanitization import compute_dedupe_key, sanitize_minimal_summary

logger = logging.getLogger("media_os_api.research")

DEFAULT_RETENTION_DAYS: int = 30


def ingest_discovery_candidate(
    session: Session,
    candidate: RawDiscoveryCandidate,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> IngestionResult:
    """Ingest a research candidate deterministically and create brand assignments idempotently.

    Invariants Enforced:
    1. Rejects policy-blocked sources (Reddit is strictly BLOCKED).
    2. Validates authorized resource namespaces.
    3. Canonical URL and dedupe key computed deterministically.
    4. SourceItem deduplicated on (source_provider, external_id).
    5. TopicCluster deduplicated on dedupe_key (conservative merging).
    6. Automatically creates exactly one BrandTopicAssignment per active BrandProfile
       with status='PENDING_EVALUATION' and relevance_score=NULL.
    7. Protects global TopicCluster.status and score_breakdown from brand interference.
    8. Idempotent under worker retries: repeated calls never duplicate sources,
       clusters, or brand assignments.
    """
    # 1. Policy gate checks
    if candidate.source_provider in BLOCKED_NAMESPACES:
        raise PolicyBlockedError(
            f"Source provider '{candidate.source_provider}' is strictly BLOCKED by policy. "
            f"Ingestion rejected."
        )

    if candidate.source_provider not in AUTHORIZED_NAMESPACES:
        raise ResourceNamespaceError(
            f"Unauthorized or unknown resource namespace '{candidate.source_provider}'. "
            f"Permitted namespaces: {sorted(AUTHORIZED_NAMESPACES)}"
        )

    now = datetime.now(UTC)

    # 2. Deterministic normalization
    canonical_url = normalize_canonical_url(candidate.raw_url)
    dedupe_key = compute_dedupe_key(
        candidate.source_provider,
        candidate.external_id,
        candidate.raw_url,
        candidate.version,
    )
    content_hash = compute_content_hash(
        f"{candidate.source_provider}:{candidate.external_id}:{candidate.title}"
    )
    minimal_summary = sanitize_minimal_summary(candidate.summary, max_length=500)

    # 3. SourceItem ingestion / deduplication
    source_item = (
        session.execute(
            select(SourceItem).where(
                SourceItem.source_provider == candidate.source_provider,
                SourceItem.external_id == candidate.external_id,
            )
        )
        .scalars()
        .first()
    )

    is_new_source = False
    if source_item is None:
        source_item = SourceItem(
            source_provider=candidate.source_provider,
            external_id=candidate.external_id,
            canonical_url=canonical_url,
            title=candidate.title[:512],
            source_label=candidate.source_label,
            published_at=candidate.published_at,
            event_at=candidate.event_at,
            fetched_at=now,
            content_hash=content_hash,
            minimal_permitted_summary=minimal_summary,
            source_policy_mode=SourcePolicyMode.EXPERIMENTAL,
            retention_expires_at=now + timedelta(days=retention_days),
            terms_checked_at=now,
        )
        session.add(source_item)
        session.flush()
        is_new_source = True
    else:
        source_item.terms_checked_at = now

    # 4. TopicCluster clustering / deduplication
    topic_cluster = (
        session.execute(select(TopicCluster).where(TopicCluster.dedupe_key == dedupe_key))
        .scalars()
        .first()
    )

    is_new_cluster = False
    assignments_created = 0

    if topic_cluster is None:
        topic_cluster = TopicCluster(
            normalized_title=candidate.title[:512],
            dedupe_key=dedupe_key,
            category=candidate.category,
            status="DISCOVERED",
            score_breakdown={},
            scoring_version="v0",
            evidence_grade="UNGRADED",
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(topic_cluster)
        session.flush()
        is_new_cluster = True

        # Link provenance
        provenance_link = TopicClusterSource(
            topic_cluster_id=topic_cluster.id,
            source_item_id=source_item.id,
        )
        session.add(provenance_link)
        session.flush()

        # 5. Automatic BrandTopicAssignment creation for ALL active brands
        active_brands = (
            session.execute(select(BrandProfile).where(BrandProfile.is_active.is_(True)))
            .scalars()
            .all()
        )

        for brand in active_brands:
            existing_assignment = (
                session.execute(
                    select(BrandTopicAssignment).where(
                        BrandTopicAssignment.brand_profile_id == brand.id,
                        BrandTopicAssignment.topic_cluster_id == topic_cluster.id,
                    )
                )
                .scalars()
                .first()
            )
            if existing_assignment is None:
                assignment = BrandTopicAssignment(
                    brand_profile_id=brand.id,
                    topic_cluster_id=topic_cluster.id,
                    editorial_status="PENDING_EVALUATION",
                    relevance_score=None,
                    score_breakdown={},
                    priority=0,
                    assigned_at=now,
                )
                session.add(assignment)
                assignments_created += 1

        session.flush()
    else:
        # Existing cluster: refresh last_seen_at; preserve status and brand assignments
        topic_cluster.last_seen_at = now

        # Ensure provenance link exists
        existing_link = (
            session.execute(
                select(TopicClusterSource).where(
                    TopicClusterSource.topic_cluster_id == topic_cluster.id,
                    TopicClusterSource.source_item_id == source_item.id,
                )
            )
            .scalars()
            .first()
        )
        if existing_link is None:
            session.add(
                TopicClusterSource(
                    topic_cluster_id=topic_cluster.id,
                    source_item_id=source_item.id,
                )
            )
            session.flush()

    return IngestionResult(
        source_item_id=source_item.id,
        topic_cluster_id=topic_cluster.id,
        assignments_created=assignments_created,
        is_new_source=is_new_source,
        is_new_cluster=is_new_cluster,
        source_provider=candidate.source_provider,
        external_id=candidate.external_id,
        dedupe_key=dedupe_key,
    )


def ingest_candidate_batch(
    session: Session,
    candidates: list[RawDiscoveryCandidate],
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> list[IngestionResult]:
    """Ingest a batch of research candidates within a single transaction."""
    results: list[IngestionResult] = []
    for candidate in candidates:
        res = ingest_discovery_candidate(session, candidate, retention_days=retention_days)
        results.append(res)
    return results
