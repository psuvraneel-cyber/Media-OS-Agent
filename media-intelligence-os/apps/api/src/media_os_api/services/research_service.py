"""Research candidate ingestion service implementing Phase 5 architectural invariants."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    # 3. SourceItem ingestion / deduplication. INSERT ... ON CONFLICT DO NOTHING makes concurrent
    #    workers safe: the loser blocks on the winner's uncommitted row, then reads it.
    inserted_source_id = session.execute(
        pg_insert(SourceItem)
        .values(
            id=uuid.uuid4(),
            source_provider=candidate.source_provider,
            external_id=candidate.external_id,
            canonical_url=canonical_url,
            title=candidate.title[:512],
            source_label=candidate.source_label[:128],
            published_at=candidate.published_at,
            event_at=candidate.event_at,
            fetched_at=now,
            content_hash=content_hash,
            minimal_permitted_summary=minimal_summary,
            source_policy_mode=SourcePolicyMode.EXPERIMENTAL,
            retention_expires_at=now + timedelta(days=retention_days),
            terms_checked_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["source_provider", "external_id"])
        .returning(SourceItem.id)
    ).scalar_one_or_none()

    is_new_source = inserted_source_id is not None
    source_item = session.execute(
        select(SourceItem).where(
            SourceItem.source_provider == candidate.source_provider,
            SourceItem.external_id == candidate.external_id,
        )
    ).scalar_one()
    if not is_new_source:
        source_item.terms_checked_at = now

    # 4. TopicCluster clustering / deduplication (global; never carries brand-specific state)
    inserted_cluster_id = session.execute(
        pg_insert(TopicCluster)
        .values(
            id=uuid.uuid4(),
            normalized_title=candidate.title[:512],
            dedupe_key=dedupe_key,
            category=candidate.category[:128],
            status="DISCOVERED",
            score_breakdown={},
            scoring_version="v0",
            evidence_grade="UNGRADED",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(TopicCluster.id)
    ).scalar_one_or_none()

    is_new_cluster = inserted_cluster_id is not None
    topic_cluster = session.execute(
        select(TopicCluster).where(TopicCluster.dedupe_key == dedupe_key)
    ).scalar_one()
    if not is_new_cluster:
        # Existing cluster: refresh last_seen_at only; status/score_breakdown stay untouched.
        topic_cluster.last_seen_at = now

    # Provenance link (idempotent)
    session.execute(
        pg_insert(TopicClusterSource)
        .values(topic_cluster_id=topic_cluster.id, source_item_id=source_item.id, created_at=now)
        .on_conflict_do_nothing(index_elements=["topic_cluster_id", "source_item_id"])
    )

    # 5. Exactly one PENDING_EVALUATION assignment per active brand, only for new clusters
    assignments_created = 0
    if is_new_cluster:
        active_brand_ids = (
            session.execute(select(BrandProfile.id).where(BrandProfile.is_active.is_(True)))
            .scalars()
            .all()
        )
        for brand_id in active_brand_ids:
            created = session.execute(
                pg_insert(BrandTopicAssignment)
                .values(
                    id=uuid.uuid4(),
                    brand_profile_id=brand_id,
                    topic_cluster_id=topic_cluster.id,
                    editorial_status="PENDING_EVALUATION",
                    relevance_score=None,
                    score_breakdown={},
                    priority=0,
                    assigned_at=now,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["brand_profile_id", "topic_cluster_id"])
                .returning(BrandTopicAssignment.id)
            ).scalar_one_or_none()
            assignments_created += int(created is not None)

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
