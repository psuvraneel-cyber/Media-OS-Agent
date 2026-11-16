"""Account-wide statistics: daily last-7 and last-30 day totals for each connected channel.

Revision ID: 0012_account_metrics
Revises: 0011_telegram_commands
Create Date: 2026-11-16

* Creates `account_metric_snapshots`: one value per (channel, metric, window 7|30, IST day), read
  from Postiz's channel analytics. These cover every post on the account, including ones posted
  outside Media OS.
* Seeds `analytics.sync_account_metrics` daily at 21:35 IST (enabled), ahead of the 21:40 post
  refresh and the 22:00 report.

Downgrade removes the schedule and drops the table.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_account_metrics"
down_revision: str | Sequence[str] | None = "0011_telegram_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")
ACCOUNT_SYNC_ID = uuid.uuid5(_NS, "analytics.sync_account_metrics")

# Next occurrence of :hour::minute IST. Times are bound parameters; the SQL is fixed text.
_INSERT_SCHEDULE = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task, "
    "'ANALYTICS_SYNC', CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', "
    "(CASE WHEN (now() AT TIME ZONE 'Asia/Kolkata') < date_trunc('day', now() AT TIME "
    "ZONE 'Asia/Kolkata') + make_interval(hours => :hour, mins => :minute) THEN "
    "date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + make_interval(hours => :hour, "
    "mins => :minute) ELSE date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + "
    "interval '1 day' + make_interval(hours => :hour, mins => :minute) END) AT TIME ZONE "
    "'Asia/Kolkata', true, now(), now()) ON CONFLICT DO NOTHING"
)


def upgrade() -> None:
    op.create_table(
        "account_metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brand_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("raw_label", sa.String(128), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            ondelete="CASCADE",
            name="fk_account_metric_channel_brand",
        ),
        sa.UniqueConstraint(
            "channel_account_id",
            "metric_key",
            "window_days",
            "observed_on",
            name="uq_account_metric_channel_metric_window_day",
        ),
        sa.CheckConstraint(
            "platform IN ('INSTAGRAM', 'FACEBOOK', 'YOUTUBE')", name="account_metric_platform_valid"
        ),
        sa.CheckConstraint("window_days IN (7, 30)", name="account_metric_window_valid"),
        sa.CheckConstraint("value >= 0", name="account_metric_value_non_negative"),
    )
    op.create_index(
        "ix_account_metric_brand_day",
        "account_metric_snapshots",
        ["brand_profile_id", "observed_on"],
    )
    op.execute(
        sa.text(_INSERT_SCHEDULE).bindparams(
            id=ACCOUNT_SYNC_ID,
            name="analytics.sync_account_metrics",
            task="media_os.analytics.sync_account_metrics",
            config=json.dumps({"kind": "daily_time", "hour": 21, "minute": 35}),
            payload=json.dumps({"trigger": "schedule"}),
            hour=21,
            minute=35,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id = :id").bindparams(id=ACCOUNT_SYNC_ID)
    )
    op.drop_index("ix_account_metric_brand_day", table_name="account_metric_snapshots")
    op.drop_table("account_metric_snapshots")
