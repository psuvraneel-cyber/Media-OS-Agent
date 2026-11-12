"""Initial Phase 4 multi-brand, durable job, outbox, and workflow schema.

Revision ID: 0001_initial_phase4_schema
Revises: None
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_phase4_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owner_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_owner_users"),
        sa.UniqueConstraint("username", name="uq_owner_users_username"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["owner_users.id"],
            name="fk_auth_sessions_owner_user_id_owner_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("jti", name="uq_auth_sessions_jti"),
    )
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_index(
        "ix_auth_sessions_owner_active",
        "auth_sessions",
        ["owner_user_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "brand_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("niche", sa.Text(), nullable=False),
        sa.Column("target_audience", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False, server_default="en"),
        sa.Column("tone_of_voice", sa.Text(), nullable=False),
        sa.Column(
            "content_pillars",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "visual_identity",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("logo_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "caption_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "hashtag_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "call_to_action_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column(
            "publishing_preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_profiles"),
        sa.UniqueConstraint("slug", name="uq_brand_profiles_slug"),
    )
    op.create_index("ix_brand_profiles_is_active", "brand_profiles", ["is_active"])

    op.create_table(
        "channel_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=False),
        sa.Column("external_username", sa.String(length=128), nullable=True),
        sa.Column("external_display_name", sa.String(length=255), nullable=True),
        sa.Column("postiz_integration_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DISCONNECTED"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="ck_channel_accounts_channel_platform_valid",
        ),
        sa.CheckConstraint(
            "status IN ('DISCONNECTED', 'CONNECTED', 'REVOKED', 'ERROR')",
            name="ck_channel_accounts_channel_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_channel_accounts_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channel_accounts"),
        sa.UniqueConstraint("id", "brand_profile_id", name="uq_channel_accounts_id_brand"),
        sa.UniqueConstraint(
            "platform",
            "external_account_id",
            name="uq_channel_accounts_platform_external_id",
        ),
        sa.UniqueConstraint(
            "postiz_integration_id",
            name="uq_channel_accounts_postiz_integration",
        ),
    )
    op.create_index(
        "ix_channel_accounts_brand_status",
        "channel_accounts",
        ["brand_profile_id", "status"],
    )

    op.create_table(
        "source_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source_label", sa.String(length=128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("minimal_permitted_summary", sa.Text(), nullable=True),
        sa.Column("source_policy_mode", sa.String(length=32), nullable=False, server_default="OFF"),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terms_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_policy_mode IN ('OFF', 'EXPERIMENTAL', 'ENABLED', 'BLOCKED')",
            name="ck_source_items_source_policy_mode_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_items"),
        sa.UniqueConstraint(
            "source_provider",
            "external_id",
            name="uq_source_items_provider_external_id",
        ),
    )
    op.create_index("ix_source_items_canonical_url", "source_items", ["canonical_url"])
    op.create_index("ix_source_items_content_hash", "source_items", ["content_hash"])
    op.create_index(
        "ix_source_items_retention_expires_at",
        "source_items",
        ["retention_expires_at"],
    )

    op.create_table(
        "topic_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_title", sa.String(length=512), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DISCOVERED"),
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("scoring_version", sa.String(length=32), nullable=False, server_default="v0"),
        sa.Column(
            "evidence_grade", sa.String(length=32), nullable=False, server_default="UNGRADED"
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_topic_clusters"),
        sa.UniqueConstraint("dedupe_key", name="uq_topic_clusters_dedupe_key"),
    )
    op.create_index("ix_topic_clusters_category", "topic_clusters", ["category"])
    op.create_index(
        "ix_topic_clusters_status_last_seen",
        "topic_clusters",
        ["status", "last_seen_at"],
    )

    op.create_table(
        "topic_cluster_sources",
        sa.Column("topic_cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["source_items.id"],
            name="fk_topic_cluster_sources_source_item_id_source_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_cluster_id"],
            ["topic_clusters.id"],
            name="fk_topic_cluster_sources_topic_cluster_id_topic_clusters",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "topic_cluster_id",
            "source_item_id",
            name="pk_topic_cluster_sources",
        ),
    )
    op.create_index(
        "ix_topic_cluster_sources_source_item_id",
        "topic_cluster_sources",
        ["source_item_id"],
    )

    op.create_table(
        "research_packets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("editorial_angle", sa.Text(), nullable=False),
        sa.Column(
            "key_findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "caveats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "unresolved_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=64),
            nullable=False,
            server_default="phase4-contract",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_research_packets_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["topic_cluster_id"],
            ["topic_clusters.id"],
            name="fk_research_packets_topic_cluster_id_topic_clusters",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_packets"),
        sa.UniqueConstraint("id", "brand_profile_id", name="uq_research_packets_id_brand"),
    )
    op.create_index(
        "ix_research_packets_brand_status",
        "research_packets",
        ["brand_profile_id", "status"],
    )
    op.create_index(
        "ix_research_packets_topic_cluster_id",
        "research_packets",
        ["topic_cluster_id"],
    )

    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_packet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "verification_status",
            sa.String(length=32),
            nullable=False,
            server_default="UNVERIFIED",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_claims_claim_confidence_range",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')",
            name="ck_claims_claim_verification_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["research_packet_id"],
            ["research_packets.id"],
            name="fk_claims_research_packet_id_research_packets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claims"),
    )
    op.create_index(
        "ix_claims_packet_status",
        "claims",
        ["research_packet_id", "verification_status"],
    )

    op.create_table(
        "claim_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supporting_excerpt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_claim_sources_claim_id_claims",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["source_items.id"],
            name="fk_claim_sources_source_item_id_source_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claim_sources"),
        sa.UniqueConstraint("claim_id", "source_item_id", name="uq_claim_sources_claim_source"),
    )
    op.create_index("ix_claim_sources_source_item_id", "claim_sources", ["source_item_id"])

    op.create_table(
        "content_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_packet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DISCOVERED"),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("angle", sa.Text(), nullable=True),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "hashtags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("script", sa.Text(), nullable=True),
        sa.Column(
            "slide_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "approval_status", sa.String(length=32), nullable=False, server_default="PENDING"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="ck_content_packages_content_platform_valid",
        ),
        sa.CheckConstraint(
            "approval_status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_content_packages_content_approval_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_content_packages_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["research_packet_id", "brand_profile_id"],
            ["research_packets.id", "research_packets.brand_profile_id"],
            name="fk_content_packages_packet_brand",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_packages"),
        sa.UniqueConstraint("id", "brand_profile_id", name="uq_content_packages_id_brand"),
    )
    op.create_index(
        "ix_content_packages_brand_status",
        "content_packages",
        ["brand_profile_id", "status"],
    )
    op.create_index(
        "ix_content_packages_research_packet_id",
        "content_packages",
        ["research_packet_id"],
    )

    op.create_table(
        "content_state_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            name="fk_content_transitions_package_brand",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_state_transitions"),
        sa.UniqueConstraint(
            "content_package_id",
            "idempotency_key",
            name="uq_content_transitions_pkg_idempotency",
        ),
    )
    op.create_index(
        "ix_content_transitions_pkg_created",
        "content_state_transitions",
        ["content_package_id", "created_at"],
    )

    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("local_path", sa.String(length=1024), nullable=False),
        sa.Column("public_url", sa.String(length=2048), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("file_size >= 0", name="ck_assets_asset_file_size_non_negative"),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_assets_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            name="fk_assets_content_brand",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assets"),
        sa.UniqueConstraint("id", "brand_profile_id", name="uq_assets_id_brand"),
    )
    op.create_index("ix_assets_brand_status", "assets", ["brand_profile_id", "status"])
    op.create_index("ix_assets_retention_expires_at", "assets", ["retention_expires_at"])

    op.create_foreign_key(
        "fk_brand_profiles_logo_asset_id",
        "brand_profiles",
        "assets",
        ["logo_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "publish_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("postiz_post_id", sa.String(length=128), nullable=True),
        sa.Column("remote_post_id", sa.String(length=128), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="ck_publish_schedules_publish_schedule_platform_valid",
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_publish_schedules_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            name="fk_publish_schedules_channel_brand",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            name="fk_publish_schedules_content_brand",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publish_schedules"),
        sa.UniqueConstraint("idempotency_key", name="uq_publish_schedules_idempotency_key"),
    )
    op.create_index(
        "ix_publish_schedules_brand_status",
        "publish_schedules",
        ["brand_profile_id", "status"],
    )
    op.create_index("ix_publish_schedules_scheduled_at", "publish_schedules", ["scheduled_at"])

    op.create_table(
        "metrics_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("metric_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("source_api", sa.String(length=64), nullable=False),
        sa.Column("authorization_class", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "platform IN ('INSTAGRAM', 'YOUTUBE')",
            name="ck_metrics_snapshots_metrics_platform_valid",
        ),
        sa.CheckConstraint(
            "authorization_class IN ('AUTHORIZED_CHANNEL', 'NON_AUTHORIZED_PUBLIC')",
            name="ck_metrics_snapshots_metrics_authorization_class_valid",
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_metrics_snapshots_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            name="fk_metrics_snapshots_channel_brand",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            name="fk_metrics_snapshots_content_brand",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metrics_snapshots"),
    )
    op.create_index(
        "ix_metrics_snapshots_brand_channel_fetched",
        "metrics_snapshots",
        ["brand_profile_id", "channel_account_id", "fetched_at"],
    )
    op.create_index(
        "ix_metrics_snapshots_retention_expires_at",
        "metrics_snapshots",
        ["retention_expires_at"],
    )

    op.create_table(
        "model_usages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("project_alias", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "audio_seconds",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "estimated_cost",
            sa.Numeric(precision=12, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("quota_status", sa.String(length=32), nullable=False),
        sa.Column("request_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_model_usages_brand_profile_id_brand_profiles",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_usages"),
    )
    op.create_index(
        "ix_model_usages_brand_created",
        "model_usages",
        ["brand_profile_id", "created_at"],
    )
    op.create_index("ix_model_usages_provider_created", "model_usages", ["provider", "created_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("request_id", sa.String(length=64), nullable=False, server_default="-"),
        sa.Column("approval_reference", sa.String(length=128), nullable=True),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index(
        "ix_audit_events_retention_expires_at",
        "audit_events",
        ["retention_expires_at"],
    )
    op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])

    op.create_table(
        "schedule_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column(
            "schedule_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "payload_template",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_schedule_definitions_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            name="fk_schedule_definitions_channel_brand",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_definitions"),
        sa.UniqueConstraint(
            "brand_profile_id",
            "name",
            name="uq_schedule_definitions_brand_name",
        ),
    )
    op.create_index(
        "ix_schedule_definitions_due",
        "schedule_definitions",
        ["is_enabled", "next_run_at"],
    )

    op.create_table(
        "job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schedule_definition_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False, server_default="control"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.String(length=192), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("maximum_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'DISPATCHED', 'RUNNING', 'SUCCEEDED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCELLED')",
            name="ck_job_runs_job_run_status_valid",
        ),
        sa.CheckConstraint(
            "maximum_attempts >= 1",
            name="ck_job_runs_job_run_max_attempts_positive",
        ),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            name="fk_job_runs_brand_profile_id_brand_profiles",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_definition_id"],
            ["schedule_definitions.id"],
            name="fk_job_runs_schedule_definition_id_schedule_definitions",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_runs"),
        sa.UniqueConstraint("idempotency_key", name="uq_job_runs_idempotency_key"),
    )
    op.create_index("ix_job_runs_brand_status", "job_runs", ["brand_profile_id", "status"])
    op.create_index("ix_job_runs_due", "job_runs", ["status", "available_at"])
    op.create_index("ix_job_runs_lease_expires", "job_runs", ["status", "lease_expires_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("maximum_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED_RETRYABLE', 'DEAD_LETTER')",
            name="ck_outbox_events_outbox_event_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["job_runs.id"],
            name="fk_outbox_events_job_run_id_job_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "event_type",
            name="uq_outbox_events_aggregate_event",
        ),
    )
    op.create_index(
        "ix_outbox_events_dispatch",
        "outbox_events",
        ["status", "next_attempt_at", "created_at"],
    )

    # PostgreSQL domain-integrity triggers
    op.execute(
        """
        CREATE OR REPLACE FUNCTION media_os_enforce_claim_source()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.verification_status = 'VERIFIED' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM claim_sources WHERE claim_id = NEW.id
                ) THEN
                    RAISE EXCEPTION
                        'Claim % cannot be VERIFIED without a ClaimSource record.',
                        NEW.id
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_claims_require_source_when_verified
        BEFORE INSERT OR UPDATE OF verification_status ON claims
        FOR EACH ROW
        EXECUTE FUNCTION media_os_enforce_claim_source();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION media_os_enforce_content_brand_immutable()
        RETURNS TRIGGER AS $$
        DECLARE
            allow_reassign text;
        BEGIN
            IF OLD.brand_profile_id IS DISTINCT FROM NEW.brand_profile_id THEN
                allow_reassign := COALESCE(
                    current_setting('media_os.allow_brand_reassignment', true),
                    'false'
                );
                IF allow_reassign <> 'true' THEN
                    RAISE EXCEPTION
                        'ContentPackage brand_profile_id is immutable without audit.'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_content_packages_brand_immutable
        BEFORE UPDATE OF brand_profile_id ON content_packages
        FOR EACH ROW
        EXECUTE FUNCTION media_os_enforce_content_brand_immutable();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION media_os_enforce_brand_logo_same_brand()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.logo_asset_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM assets
                    WHERE id = NEW.logo_asset_id AND brand_profile_id = NEW.id
                ) THEN
                    RAISE EXCEPTION
                        'BrandProfile logo_asset_id % must belong to brand %.',
                        NEW.logo_asset_id,
                        NEW.id
                        USING ERRCODE = '23503';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_brand_profiles_logo_same_brand
        BEFORE INSERT OR UPDATE OF logo_asset_id ON brand_profiles
        FOR EACH ROW
        EXECUTE FUNCTION media_os_enforce_brand_logo_same_brand();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_brand_profiles_logo_same_brand ON brand_profiles;")
    op.execute("DROP FUNCTION IF EXISTS media_os_enforce_brand_logo_same_brand();")
    op.execute("DROP TRIGGER IF EXISTS trg_content_packages_brand_immutable ON content_packages;")
    op.execute("DROP FUNCTION IF EXISTS media_os_enforce_content_brand_immutable();")
    op.execute("DROP TRIGGER IF EXISTS trg_claims_require_source_when_verified ON claims;")
    op.execute("DROP FUNCTION IF EXISTS media_os_enforce_claim_source();")

    op.drop_index("ix_outbox_events_dispatch", table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index("ix_job_runs_lease_expires", table_name="job_runs")
    op.drop_index("ix_job_runs_due", table_name="job_runs")
    op.drop_index("ix_job_runs_brand_status", table_name="job_runs")
    op.drop_table("job_runs")

    op.drop_index("ix_schedule_definitions_due", table_name="schedule_definitions")
    op.drop_table("schedule_definitions")

    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_index("ix_audit_events_retention_expires_at", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_model_usages_provider_created", table_name="model_usages")
    op.drop_index("ix_model_usages_brand_created", table_name="model_usages")
    op.drop_table("model_usages")

    op.drop_index("ix_metrics_snapshots_retention_expires_at", table_name="metrics_snapshots")
    op.drop_index("ix_metrics_snapshots_brand_channel_fetched", table_name="metrics_snapshots")
    op.drop_table("metrics_snapshots")

    op.drop_index("ix_publish_schedules_scheduled_at", table_name="publish_schedules")
    op.drop_index("ix_publish_schedules_brand_status", table_name="publish_schedules")
    op.drop_table("publish_schedules")

    op.drop_constraint("fk_brand_profiles_logo_asset_id", "brand_profiles", type_="foreignkey")

    op.drop_index("ix_assets_retention_expires_at", table_name="assets")
    op.drop_index("ix_assets_brand_status", table_name="assets")
    op.drop_table("assets")

    op.drop_index("ix_content_transitions_pkg_created", table_name="content_state_transitions")
    op.drop_table("content_state_transitions")

    op.drop_index("ix_content_packages_research_packet_id", table_name="content_packages")
    op.drop_index("ix_content_packages_brand_status", table_name="content_packages")
    op.drop_table("content_packages")

    op.drop_index("ix_claim_sources_source_item_id", table_name="claim_sources")
    op.drop_table("claim_sources")

    op.drop_index("ix_claims_packet_status", table_name="claims")
    op.drop_table("claims")

    op.drop_index("ix_research_packets_topic_cluster_id", table_name="research_packets")
    op.drop_index("ix_research_packets_brand_status", table_name="research_packets")
    op.drop_table("research_packets")

    op.drop_index("ix_topic_cluster_sources_source_item_id", table_name="topic_cluster_sources")
    op.drop_table("topic_cluster_sources")

    op.drop_index("ix_topic_clusters_status_last_seen", table_name="topic_clusters")
    op.drop_index("ix_topic_clusters_category", table_name="topic_clusters")
    op.drop_table("topic_clusters")

    op.drop_index("ix_source_items_retention_expires_at", table_name="source_items")
    op.drop_index("ix_source_items_content_hash", table_name="source_items")
    op.drop_index("ix_source_items_canonical_url", table_name="source_items")
    op.drop_table("source_items")

    op.drop_index("ix_channel_accounts_brand_status", table_name="channel_accounts")
    op.drop_table("channel_accounts")

    op.drop_index("ix_brand_profiles_is_active", table_name="brand_profiles")
    op.drop_table("brand_profiles")

    op.drop_index("ix_auth_sessions_owner_active", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_table("owner_users")
