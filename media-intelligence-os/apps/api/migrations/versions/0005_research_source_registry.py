"""Add research source registry/health, fetch checkpoints, and global research schedules.

Revision ID: 0005_research_source_registry
Revises: 0004_add_brand_topic_assignments
Create Date: 2026-11-13

Additive migration (owner-approved schema, see docs/PHASE_5_COMPLETION_PLAN.md):

* `research_sources` — one row per source family: policy mode, owner live-fetch approval, non-secret
  config, and health. Reddit is locked to BLOCKED by a CHECK constraint.
* `research_source_checkpoints` — per-stream cursor + conditional-request validators.
* `schedule_definitions.brand_profile_id` becomes nullable so research schedules can be global;
  global schedules cannot bind a channel and have globally unique names.
* Seeds the six registry rows (five first-cohort sources OFF, Reddit BLOCKED) and five research
  fetch schedules with `is_enabled = false`.

Column naming note: `source_code` / `stream_name` (not `*_key`) because the job-payload secret guard
and log redaction treat every `*_key` field as a credential.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_research_source_registry"
down_revision: str | Sequence[str] | None = "0004_add_brand_topic_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEDULE_NAMESPACE = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")

# (source_code, display_name, policy_mode, health_status, config)
_SOURCES: tuple[tuple[str, str, str, str, dict[str, object]], ...] = (
    (
        "HACKERNEWS",
        "Hacker News (official Firebase API)",
        "OFF",
        "DISABLED",
        {
            "streams": ["topstories", "newstories", "beststories"],
            "max_items_per_stream": 30,
            "min_interval_ms": 250,
        },
    ),
    (
        "GITHUB",
        "GitHub (official REST API)",
        "OFF",
        "DISABLED",
        {
            "search_queries": ["topic:llm", "topic:ai-agents", "topic:developer-tools"],
            "max_repositories_per_query": 30,
            "watched_repositories": [],
            "max_releases_per_repository": 5,
            "min_interval_ms": 6500,
        },
    ),
    (
        "HUGGINGFACE",
        "Hugging Face Hub (official API, metadata only)",
        "OFF",
        "DISABLED",
        {"streams": ["models", "datasets", "spaces"], "limit": 30, "min_interval_ms": 1000},
    ),
    (
        "ARXIV",
        "arXiv (official Atom API, no PDFs)",
        "OFF",
        "DISABLED",
        {
            "categories": ["cs.AI", "cs.CL", "cs.LG", "cs.SE"],
            "max_results": 50,
            "min_interval_ms": 3100,
        },
    ),
    (
        "OPENAI_NEWS",
        "OpenAI News (official RSS feed)",
        "OFF",
        "DISABLED",
        {"feed_url": "https://openai.com/news/rss.xml", "min_interval_ms": 1000},
    ),
    ("REDDIT", "Reddit (BLOCKED by policy SRC-01)", "BLOCKED", "BLOCKED", {}),
)

# (source_code, schedule_config) — all seeded disabled.
_SCHEDULES: tuple[tuple[str, dict[str, object]], ...] = (
    ("HACKERNEWS", {"kind": "interval", "seconds": 3600}),
    ("GITHUB", {"kind": "interval", "seconds": 21600}),
    ("HUGGINGFACE", {"kind": "interval", "seconds": 21600}),
    ("ARXIV", {"kind": "daily_time", "hour": 7, "minute": 0}),
    ("OPENAI_NEWS", {"kind": "interval", "seconds": 10800}),
)


def schedule_name(source_code: str) -> str:
    return f"research.fetch.{source_code.lower()}"


def schedule_id(source_code: str) -> uuid.UUID:
    return uuid.uuid5(_SCHEDULE_NAMESPACE, schedule_name(source_code))


def _timestamps() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s';")

    op.create_table(
        "research_sources",
        sa.Column("source_code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("policy_mode", sa.String(length=16), nullable=False, server_default="OFF"),
        sa.Column(
            "live_fetch_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("health_status", sa.String(length=16), nullable=False, server_default="UNKNOWN"),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "last_run_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("backoff_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("source_code", name="pk_research_sources"),
        sa.CheckConstraint(
            "source_code ~ '^[A-Z][A-Z0-9_]{1,31}$'",
            name="ck_research_sources_source_code_format",
        ),
        sa.CheckConstraint(
            "policy_mode IN ('OFF', 'EXPERIMENTAL', 'ENABLED', 'BLOCKED')",
            name="ck_research_sources_policy_mode_valid",
        ),
        sa.CheckConstraint(
            "source_code <> 'REDDIT' OR policy_mode = 'BLOCKED'",
            name="ck_research_sources_reddit_blocked",
        ),
        sa.CheckConstraint(
            "NOT live_fetch_approved OR policy_mode IN ('EXPERIMENTAL', 'ENABLED')",
            name="ck_research_sources_live_fetch_requires_active_mode",
        ),
        sa.CheckConstraint(
            "NOT live_fetch_approved OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_research_sources_live_fetch_approval_recorded",
        ),
        sa.CheckConstraint(
            "health_status IN ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'FAILING', 'DISABLED', 'BLOCKED')",
            name="ck_research_sources_health_status_valid",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_research_sources_consecutive_failures_nonnegative",
        ),
    )

    op.create_table(
        "research_source_checkpoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_code", sa.String(length=32), nullable=False),
        sa.Column("stream_name", sa.String(length=128), nullable=False),
        sa.Column(
            "cursor",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("etag", sa.String(length=512), nullable=True),
        sa.Column("last_modified", sa.String(length=128), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_research_source_checkpoints"),
        sa.ForeignKeyConstraint(
            ["source_code"],
            ["research_sources.source_code"],
            name="fk_research_source_checkpoints_source_code_research_sources",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_code", "stream_name", name="uq_research_source_checkpoints_source_stream"
        ),
    )

    # Global (brand-less) schedules for shared research.
    op.alter_column("schedule_definitions", "brand_profile_id", nullable=True)
    op.create_check_constraint(
        "ck_schedule_definitions_global_has_no_channel",
        "schedule_definitions",
        "brand_profile_id IS NOT NULL OR channel_account_id IS NULL",
    )
    op.create_index(
        "uq_schedule_definitions_global_name",
        "schedule_definitions",
        ["name"],
        unique=True,
        postgresql_where=sa.text("brand_profile_id IS NULL"),
    )

    sources_table = sa.table(
        "research_sources",
        sa.column("source_code", sa.String),
        sa.column("display_name", sa.String),
        sa.column("policy_mode", sa.String),
        sa.column("health_status", sa.String),
        sa.column("config", postgresql.JSONB),
    )
    op.bulk_insert(
        sources_table,
        [
            {
                "source_code": code,
                "display_name": name,
                "policy_mode": mode,
                "health_status": health,
                "config": config,
            }
            for code, name, mode, health, config in _SOURCES
        ],
    )

    for code, schedule_config in _SCHEDULES:
        op.execute(
            sa.text(
                "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
                "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
                "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task_name, "
                "'RESEARCH_FETCH', CAST(:config AS jsonb), CAST(:payload AS jsonb), "
                "'Asia/Kolkata', now(), false, now(), now()) ON CONFLICT DO NOTHING"
            ).bindparams(
                id=schedule_id(code),
                name=schedule_name(code),
                task_name=f"media_os.research.fetch.{code.lower()}",
                config=json.dumps(schedule_config),
                payload=json.dumps({"source": code, "trigger": "schedule"}),
            )
        )


def downgrade() -> None:
    """Drop registry/checkpoints and the seeded schedules.

    Refuses if any *other* global schedule exists, because `brand_profile_id` could not be made
    NOT NULL again without deleting owner data. Checkpoints/health are rebuildable (a re-fetch is
    absorbed by SourceItem/TopicCluster deduplication); no editorial data is touched.
    """
    op.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s';")

    seeded_ids = [str(schedule_id(code)) for code, _ in _SCHEDULES]
    bind = op.get_bind()
    # Seeded schedules may already have produced JobRuns; detach them (FK is ON DELETE SET NULL).
    bind.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": seeded_ids},
    )
    remaining = bind.execute(
        sa.text("SELECT count(*) FROM schedule_definitions WHERE brand_profile_id IS NULL")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"Refusing downgrade: {remaining} non-seeded global schedule(s) exist. Remove or "
            "re-scope them explicitly (with owner approval) before downgrading 0005."
        )

    op.drop_index("uq_schedule_definitions_global_name", table_name="schedule_definitions")
    op.drop_constraint(
        "ck_schedule_definitions_global_has_no_channel", "schedule_definitions", type_="check"
    )
    op.alter_column("schedule_definitions", "brand_profile_id", nullable=False)
    op.drop_table("research_source_checkpoints")
    op.drop_table("research_sources")
