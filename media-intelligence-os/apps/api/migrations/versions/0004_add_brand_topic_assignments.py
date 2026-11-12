"""Add brand_topic_assignments table and enforce ResearchPacket assignment integrity.

Revision ID: 0004_add_brand_topic_assignments
Revises: 0003_domain_integrity_hardening
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_brand_topic_assignments"
down_revision: str | Sequence[str] | None = "0003_domain_integrity_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALID_EDITORIAL_STATUSES_SQL = (
    "('PENDING_EVALUATION', 'CONSIDERED', 'SHORTLISTED', 'APPROVED_FOR_RESEARCH', 'REJECTED', 'EXPIRED')"
)


def upgrade() -> None:
    # Protect against hung migrations by enforcing short lock and statement timeouts
    op.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s';")

    op.create_table(
        "brand_topic_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "editorial_status",
            sa.String(length=32),
            nullable=False,
            server_default="PENDING_EVALUATION",
        ),
        sa.Column("relevance_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("editorial_notes", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_brand_topic_assignments_brand_profile_id_brand_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["topic_cluster_id"],
            ["topic_clusters.id"],
            name="fk_brand_topic_assignments_topic_cluster_id_topic_clusters",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_topic_assignments"),
        sa.UniqueConstraint(
            "brand_profile_id",
            "topic_cluster_id",
            name="uq_brand_topic_assignments_brand_topic",
        ),
        sa.UniqueConstraint(
            "id",
            "brand_profile_id",
            name="uq_brand_topic_assignments_id_brand",
        ),
        sa.CheckConstraint(
            f"editorial_status IN {_VALID_EDITORIAL_STATUSES_SQL}",
            name="ck_brand_topic_assignments_editorial_status_valid",
        ),
        sa.CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0.0000 AND relevance_score <= 1.0000)",
            name="ck_brand_topic_assignments_relevance_score_range",
        ),
    )

    op.create_index(
        "ix_brand_topic_assignments_brand_status",
        "brand_topic_assignments",
        ["brand_profile_id", "editorial_status"],
    )
    op.create_index(
        "ix_brand_topic_assignments_brand_score",
        "brand_topic_assignments",
        ["brand_profile_id", "relevance_score"],
    )
    op.create_index(
        "ix_brand_topic_assignments_topic_cluster_id",
        "brand_topic_assignments",
        ["topic_cluster_id"],
    )

    # Enforce that every ResearchPacket references an existing BrandTopicAssignment
    op.create_foreign_key(
        "fk_research_packets_brand_topic",
        "research_packets",
        "brand_topic_assignments",
        ["brand_profile_id", "topic_cluster_id"],
        ["brand_profile_id", "topic_cluster_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s';")

    op.drop_constraint("fk_research_packets_brand_topic", "research_packets", type_="foreignkey")
    op.drop_index(
        "ix_brand_topic_assignments_topic_cluster_id", table_name="brand_topic_assignments"
    )
    op.drop_index("ix_brand_topic_assignments_brand_score", table_name="brand_topic_assignments")
    op.drop_index("ix_brand_topic_assignments_brand_status", table_name="brand_topic_assignments")
    op.drop_table("brand_topic_assignments")
