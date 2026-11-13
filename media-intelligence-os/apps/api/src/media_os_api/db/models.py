"""Normalized PostgreSQL ORM entities for Media Intelligence OS (Phase 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_os_api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class OwnerUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-owner authentication identity persisted in PostgreSQL."""

    __tablename__ = "owner_users"

    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="owner_user",
        cascade="all, delete-orphan",
    )


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent authenticated session & revocation registry keyed by `jti`."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_owner_active", "owner_user_id", "revoked_at", "expires_at"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("owner_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    owner_user: Mapped[OwnerUser] = relationship(back_populates="sessions")


class BrandProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Independent brand identity controlling editorial voice, channels, and content isolation."""

    __tablename__ = "brand_profiles"
    __table_args__ = (Index("ix_brand_profiles_is_active", "is_active"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    niche: Mapped[str] = mapped_column(Text, nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="en")
    tone_of_voice: Mapped[str] = mapped_column(Text, nullable=False)
    content_pillars: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    visual_identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    logo_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "assets.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_brand_profiles_logo_asset_id",
        ),
        nullable=True,
    )
    caption_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    hashtag_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    call_to_action_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    publishing_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    channels: Mapped[list[ChannelAccount]] = relationship(back_populates="brand_profile")
    topic_assignments: Mapped[list[BrandTopicAssignment]] = relationship(
        back_populates="brand_profile"
    )


class ChannelAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Social destination account bound strictly to one BrandProfile (Postiz owns OAuth)."""

    __tablename__ = "channel_accounts"
    __table_args__ = (
        UniqueConstraint("id", "brand_profile_id", name="uq_channel_accounts_id_brand"),
        UniqueConstraint("postiz_integration_id", name="uq_channel_accounts_postiz_integration"),
        UniqueConstraint(
            "platform",
            "external_account_id",
            name="uq_channel_accounts_platform_external_id",
        ),
        CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="channel_platform_valid",
        ),
        CheckConstraint(
            "status IN ('DISCONNECTED', 'CONNECTED', 'REVOKED', 'ERROR')",
            name="channel_status_valid",
        ),
        Index("ix_channel_accounts_brand_status", "brand_profile_id", "status"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    postiz_integration_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCONNECTED")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    brand_profile: Mapped[BrandProfile] = relationship(back_populates="channels")


class SourceItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shared normalized discovery source metadata without full third-party body retention."""

    __tablename__ = "source_items"
    __table_args__ = (
        UniqueConstraint(
            "source_provider",
            "external_id",
            name="uq_source_items_provider_external_id",
        ),
        CheckConstraint(
            "source_policy_mode IN ('OFF', 'EXPERIMENTAL', 'ENABLED', 'BLOCKED')",
            name="source_policy_mode_valid",
        ),
        Index("ix_source_items_canonical_url", "canonical_url"),
        Index("ix_source_items_content_hash", "content_hash"),
        Index("ix_source_items_retention_expires_at", "retention_expires_at"),
    )

    source_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_label: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    minimal_permitted_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_policy_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="OFF")
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terms_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResearchSource(TimestampMixin, Base):
    """Global research source registry row: policy mode, owner approval, config and health."""

    __tablename__ = "research_sources"
    __table_args__ = (
        CheckConstraint("source_code ~ '^[A-Z][A-Z0-9_]{1,31}$'", name="source_code_format"),
        CheckConstraint(
            "policy_mode IN ('OFF', 'EXPERIMENTAL', 'ENABLED', 'BLOCKED')",
            name="policy_mode_valid",
        ),
        CheckConstraint(
            "source_code <> 'REDDIT' OR policy_mode = 'BLOCKED'", name="reddit_blocked"
        ),
        CheckConstraint(
            "NOT live_fetch_approved OR policy_mode IN ('EXPERIMENTAL', 'ENABLED')",
            name="live_fetch_requires_active_mode",
        ),
        CheckConstraint(
            "NOT live_fetch_approved OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="live_fetch_approval_recorded",
        ),
        CheckConstraint(
            "health_status IN ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'FAILING', 'DISABLED', 'BLOCKED')",
            name="health_status_valid",
        ),
        CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_nonnegative"),
    )

    source_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="OFF")
    live_fetch_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_run_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchSourceCheckpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-stream fetch cursor and HTTP conditional-request validators for a research source."""

    __tablename__ = "research_source_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "source_code", "stream_name", name="uq_research_source_checkpoints_source_stream"
        ),
    )

    source_code: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("research_sources.source_code", ondelete="RESTRICT"),
        nullable=False,
    )
    stream_name: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TopicCluster(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shared deduplicated topic cluster aggregated from one or more SourceItem records."""

    __tablename__ = "topic_clusters"
    __table_args__ = (
        Index("ix_topic_clusters_status_last_seen", "status", "last_seen_at"),
        Index("ix_topic_clusters_category", "category"),
    )

    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCOVERED")
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v0")
    evidence_grade: Mapped[str] = mapped_column(String(32), nullable=False, default="UNGRADED")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    assignments: Mapped[list[BrandTopicAssignment]] = relationship(back_populates="topic_cluster")


class TopicClusterSource(Base):
    """Many-to-many provenance link between TopicCluster and SourceItem."""

    __tablename__ = "topic_cluster_sources"
    __table_args__ = (Index("ix_topic_cluster_sources_source_item_id", "source_item_id"),)

    topic_cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("topic_clusters.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("source_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class BrandTopicAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Brand-specific editorial screening and priority assignment for a shared TopicCluster."""

    __tablename__ = "brand_topic_assignments"
    __table_args__ = (
        UniqueConstraint(
            "brand_profile_id",
            "topic_cluster_id",
            name="uq_brand_topic_assignments_brand_topic",
        ),
        UniqueConstraint(
            "id",
            "brand_profile_id",
            name="uq_brand_topic_assignments_id_brand",
        ),
        CheckConstraint(
            "editorial_status IN ('PENDING_EVALUATION', 'CONSIDERED', 'SHORTLISTED', "
            "'APPROVED_FOR_RESEARCH', 'REJECTED', 'EXPIRED')",
            name="ck_brand_topic_assignments_editorial_status_valid",
        ),
        CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0.0000 AND relevance_score <= 1.0000)",
            name="ck_brand_topic_assignments_relevance_score_range",
        ),
        Index("ix_brand_topic_assignments_brand_status", "brand_profile_id", "editorial_status"),
        Index("ix_brand_topic_assignments_brand_score", "brand_profile_id", "relevance_score"),
        Index("ix_brand_topic_assignments_topic_cluster_id", "topic_cluster_id"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    topic_cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("topic_clusters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    editorial_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING_EVALUATION"
    )
    relevance_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True, default=None
    )
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    editorial_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    brand_profile: Mapped[BrandProfile] = relationship(back_populates="topic_assignments")
    topic_cluster: Mapped[TopicCluster] = relationship(back_populates="assignments")


class ResearchPacket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Brand-specific primary-source research dossier compiled from a TopicCluster."""

    __tablename__ = "research_packets"
    __table_args__ = (
        UniqueConstraint("id", "brand_profile_id", name="uq_research_packets_id_brand"),
        ForeignKeyConstraint(
            ["brand_profile_id", "topic_cluster_id"],
            [
                "brand_topic_assignments.brand_profile_id",
                "brand_topic_assignments.topic_cluster_id",
            ],
            name="fk_research_packets_brand_topic",
            ondelete="RESTRICT",
        ),
        Index("ix_research_packets_brand_status", "brand_profile_id", "status"),
        Index("ix_research_packets_topic_cluster_id", "topic_cluster_id"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    topic_cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("topic_clusters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    editorial_angle: Mapped[str] = mapped_column(Text, nullable=False)
    key_findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    caveats: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    unresolved_questions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    source_manifest: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    prompt_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="phase4-contract"
    )


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Factual claim inside a ResearchPacket; cannot be VERIFIED without ClaimSource."""

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="claim_confidence_range",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')",
            name="claim_verification_status_valid",
        ),
        Index("ix_claims_packet_status", "research_packet_id", "verification_status"),
    )

    research_packet_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("research_packets.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=Decimal("0"))
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNVERIFIED"
    )

    sources: Mapped[list[ClaimSource]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ClaimSource(UUIDPrimaryKeyMixin, Base):
    """Explicit provenance binding linking a Claim to at least one SourceItem."""

    __tablename__ = "claim_sources"
    __table_args__ = (
        UniqueConstraint("claim_id", "source_item_id", name="uq_claim_sources_claim_source"),
        Index("ix_claim_sources_source_item_id", "source_item_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("source_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supporting_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )

    claim: Mapped[Claim] = relationship(back_populates="sources")


class ContentPackage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Brand-owned content production artifact progressing through the validated state machine."""

    __tablename__ = "content_packages"
    __table_args__ = (
        UniqueConstraint("id", "brand_profile_id", name="uq_content_packages_id_brand"),
        ForeignKeyConstraint(
            ["research_packet_id", "brand_profile_id"],
            ["research_packets.id", "research_packets.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_content_packages_packet_brand",
        ),
        CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="content_platform_valid",
        ),
        CheckConstraint(
            "approval_status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="content_approval_status_valid",
        ),
        CheckConstraint(
            "status IN ('DISCOVERED', 'NORMALIZED', 'DEDUPED', 'RANKED', 'RESEARCHED', "
            "'DRAFTED', 'RENDERED', 'QA_PASSED', 'POSTIZ_DRAFT', 'USER_APPROVED', "
            "'SCHEDULED', 'PUBLISHING', 'PUBLISHED', 'METRICS_CAPTURED', 'ARCHIVED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'REJECTED', 'PAUSED')",
            name="content_status_valid",
        ),
        CheckConstraint(
            "previous_status IS NULL OR previous_status IN ('DISCOVERED', 'NORMALIZED', "
            "'DEDUPED', 'RANKED', 'RESEARCHED', 'DRAFTED', 'RENDERED', 'QA_PASSED', "
            "'POSTIZ_DRAFT', 'USER_APPROVED', 'SCHEDULED', 'PUBLISHING', 'PUBLISHED', "
            "'METRICS_CAPTURED', 'ARCHIVED', 'FAILED_RETRYABLE', 'FAILED_FINAL', "
            "'REJECTED', 'PAUSED')",
            name="content_previous_status_valid",
        ),
        Index("ix_content_packages_brand_status", "brand_profile_id", "status"),
        Index("ix_content_packages_research_packet_id", "research_packet_id"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    research_packet_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCOVERED")
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    slide_data: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    source_manifest: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ContentStateTransition(UUIDPrimaryKeyMixin, Base):
    """Immutable audit trail of every ContentPackage workflow state transition."""

    __tablename__ = "content_state_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_content_transitions_package_brand",
        ),
        UniqueConstraint(
            "content_package_id",
            "idempotency_key",
            name="uq_content_transitions_pkg_idempotency",
        ),
        CheckConstraint(
            "to_status IN ('DISCOVERED', 'NORMALIZED', 'DEDUPED', 'RANKED', 'RESEARCHED', "
            "'DRAFTED', 'RENDERED', 'QA_PASSED', 'POSTIZ_DRAFT', 'USER_APPROVED', "
            "'SCHEDULED', 'PUBLISHING', 'PUBLISHED', 'METRICS_CAPTURED', 'ARCHIVED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'REJECTED', 'PAUSED')",
            name="transition_to_status_valid",
        ),
        Index(
            "ix_content_transitions_pkg_created",
            "content_package_id",
            "created_at",
        ),
    )

    content_package_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    brand_profile_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata for local media files strictly isolated by BrandProfile."""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("id", "brand_profile_id", name="uq_assets_id_brand"),
        ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_assets_content_brand",
        ),
        CheckConstraint("file_size >= 0", name="asset_file_size_non_negative"),
        Index("ix_assets_brand_status", "brand_profile_id", "status"),
        Index("ix_assets_retention_expires_at", "retention_expires_at"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_package_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    local_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    public_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PublishSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Intended publishing destination and state contract (publishing deferred to Phase 8)."""

    __tablename__ = "publish_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_publish_schedules_channel_brand",
        ),
        ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_publish_schedules_content_brand",
        ),
        CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="publish_schedule_platform_valid",
        ),
        Index("ix_publish_schedules_brand_status", "brand_profile_id", "status"),
        Index("ix_publish_schedules_scheduled_at", "scheduled_at"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_account_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_package_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    postiz_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remote_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MetricsSnapshot(UUIDPrimaryKeyMixin, Base):
    """Per-brand, per-channel metrics record with explicit API/YouTube retention policies."""

    __tablename__ = "metrics_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_metrics_snapshots_channel_brand",
        ),
        ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_metrics_snapshots_content_brand",
        ),
        CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="metrics_platform_valid",
        ),
        CheckConstraint(
            "authorization_class IN ('AUTHORIZED_CHANNEL', 'NON_AUTHORIZED_PUBLIC')",
            name="metrics_authorization_class_valid",
        ),
        Index(
            "ix_metrics_snapshots_brand_channel_fetched",
            "brand_profile_id",
            "channel_account_id",
            "fetched_at",
        ),
        Index("ix_metrics_snapshots_retention_expires_at", "retention_expires_at"),
    )

    brand_profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_account_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_package_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    source_api: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_class: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class ModelUsage(UUIDPrimaryKeyMixin, Base):
    """Future-ready LLM/TTS quota & cost ledger (no active Gemini calls in Phase 4)."""

    __tablename__ = "model_usages"
    __table_args__ = (
        Index("ix_model_usages_brand_created", "brand_profile_id", "created_at"),
        Index("ix_model_usages_provider_created", "provider", "created_at"),
    )

    brand_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    project_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audio_seconds: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0")
    )
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    quota_status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable sanitized audit trail persisted for 365 days."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_target", "target_type", "target_id"),
        Index("ix_audit_events_timestamp", "timestamp"),
        Index("ix_audit_events_action", "action"),
        Index("ix_audit_events_retention_expires_at", "retention_expires_at"),
    )

    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="-")
    approval_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScheduleDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent PostgreSQL schedule definition evaluated by the scheduler process."""

    __tablename__ = "schedule_definitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_schedule_definitions_channel_brand",
        ),
        UniqueConstraint(
            "brand_profile_id",
            "name",
            name="uq_schedule_definitions_brand_name",
        ),
        CheckConstraint(
            "brand_profile_id IS NOT NULL OR channel_account_id IS NULL",
            name="global_has_no_channel",
        ),
        Index("ix_schedule_definitions_due", "is_enabled", "next_run_at"),
        Index(
            "uq_schedule_definitions_global_name",
            "name",
            unique=True,
            postgresql_where=text("brand_profile_id IS NULL"),
        ),
    )

    # NULL = global schedule (shared research); brand-scoped otherwise (migration 0005).
    brand_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="RESTRICT"),
        nullable=True,
    )
    channel_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schedule_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_template: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class JobRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable background job ledger enforcing idempotency, bounded retries, and leases."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'DISPATCHED', 'RUNNING', 'SUCCEEDED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCELLED')",
            name="job_run_status_valid",
        ),
        CheckConstraint("maximum_attempts >= 1", name="job_run_max_attempts_positive"),
        Index("ix_job_runs_due", "status", "available_at"),
        Index("ix_job_runs_lease_expires", "status", "lease_expires_at"),
        Index("ix_job_runs_brand_status", "brand_profile_id", "status"),
    )

    brand_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brand_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    schedule_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("schedule_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False, default="control")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    maximum_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class OutboxEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Transactional outbox record committed atomically alongside its JobRun."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "event_type",
            name="uq_outbox_events_aggregate_event",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED_RETRYABLE', 'DEAD_LETTER')",
            name="outbox_event_status_valid",
        ),
        Index("ix_outbox_events_dispatch", "status", "next_attempt_at", "created_at"),
    )

    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("job_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    maximum_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
