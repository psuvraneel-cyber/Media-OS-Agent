"""Views analytics: timestamped per-post metric observations (roadmap N5 groundwork).

Revision ID: 0008_post_metric_observations
Revises: 0007_facebook_platform
Create Date: 2026-11-15

Adds `post_metric_observations`: one platform-reported value per published post, metric and
observed hour. Meta post insights are lifetime cumulative, so increments are derived at read time
(never summed). No existing rows are touched.

Also:
* `publish_schedules` gains `metrics_checked_at`, `metrics_status` (OK / NO_DATA / MISSING / ERROR)
  and `metrics_detail`, so the sync can space out checks of older posts and the UI can say why a
  post has no numbers.
* Seeds `analytics.sync_post_metrics` (every 6 h; `is_enabled = false`, the owner enables it, as
  in 0007) and `maintenance.purge_job_history` (daily 03:30 IST, enabled: it deletes only finished
  runs of recurring schedules older than the retention window).
* Marks the unused `metrics_snapshots` table deprecated (table comment). It is kept because its
  retention columns encode the YouTube API data-refresh policy needed when YouTube is connected.

Downgrade refuses while observations exist, so collected history is never dropped silently.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_post_metric_observations"
down_revision: str | Sequence[str] | None = "0007_facebook_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "post_metric_observations"
SYNC_SCHEDULE_ID = uuid.uuid5(
    uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47"), "analytics.sync_post_metrics"
)
PURGE_SCHEDULE_ID = uuid.uuid5(
    uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47"), "maintenance.purge_job_history"
)
_SCHEDULE_SQL = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task, :type, "
    "CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', "
    # First run at the next 03:30 IST, not immediately on deploy.
    "(date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + interval '1 day 3 hours 30 minutes')"
    " AT TIME ZONE 'Asia/Kolkata', :enabled, now(), now()) ON CONFLICT DO NOTHING"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publish_schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("value_kind", sa.String(16), nullable=False, server_default="CUMULATIVE"),
        sa.Column("value", sa.Numeric(18, 4), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_label", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.ForeignKeyConstraint(
            ["brand_profile_id"],
            ["brand_profiles.id"],
            ondelete="RESTRICT",
            name=op.f(f"fk_{TABLE}_brand_profile_id_brand_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["publish_schedule_id"],
            ["publish_schedules.id"],
            ondelete="RESTRICT",
            name=op.f(f"fk_{TABLE}_publish_schedule_id_publish_schedules"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_account_id", "brand_profile_id"],
            ["channel_accounts.id", "channel_accounts.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_post_metric_obs_channel_brand",
        ),
        sa.ForeignKeyConstraint(
            ["content_package_id", "brand_profile_id"],
            ["content_packages.id", "content_packages.brand_profile_id"],
            ondelete="RESTRICT",
            name="fk_post_metric_obs_content_brand",
        ),
        sa.UniqueConstraint(
            "publish_schedule_id",
            "metric_key",
            "observed_at",
            name="uq_post_metric_obs_schedule_metric_time",
        ),
        sa.CheckConstraint(
            "platform IN ('INSTAGRAM', 'FACEBOOK', 'YOUTUBE')",
            name=op.f(f"ck_{TABLE}_post_metric_platform_valid"),
        ),
        sa.CheckConstraint(
            "value_kind IN ('CUMULATIVE', 'PERIOD')",
            name=op.f(f"ck_{TABLE}_post_metric_kind_valid"),
        ),
        sa.CheckConstraint("value >= 0", name=op.f(f"ck_{TABLE}_post_metric_value_non_negative")),
    )
    op.create_index("ix_post_metric_obs_brand_observed", TABLE, ["brand_profile_id", "observed_at"])
    op.add_column(
        "publish_schedules",
        sa.Column("metrics_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("publish_schedules", sa.Column("metrics_status", sa.String(16), nullable=True))
    op.add_column("publish_schedules", sa.Column("metrics_detail", sa.String(200), nullable=True))
    op.create_check_constraint(
        op.f("ck_publish_schedules_publish_schedule_metrics_status_valid"),
        "publish_schedules",
        "metrics_status IS NULL OR metrics_status IN ('OK', 'NO_DATA', 'MISSING', 'ERROR')",
    )
    op.execute(
        sa.text(
            "COMMENT ON TABLE metrics_snapshots IS 'DEPRECATED (0008): unused. Post analytics "
            "live in post_metric_observations. Kept for the YouTube data-retention policy.'"
        )
    )
    op.execute(
        sa.text(_SCHEDULE_SQL).bindparams(
            id=PURGE_SCHEDULE_ID,
            name="maintenance.purge_job_history",
            task="media_os.maintenance.purge_job_history",
            type="MAINTENANCE",
            config=json.dumps({"kind": "daily_time", "hour": 3, "minute": 30}),
            payload=json.dumps({"trigger": "schedule"}),
            enabled=True,
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
            "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
            "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, "
            "'analytics.sync_post_metrics', 'media_os.analytics.sync_post_metrics', "
            "'ANALYTICS_SYNC', CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', "
            "now(), false, now(), now()) ON CONFLICT DO NOTHING"
        ).bindparams(
            id=SYNC_SCHEDULE_ID,
            config=json.dumps({"kind": "interval", "seconds": 21600}),
            payload=json.dumps({"trigger": "schedule"}),
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar_one()  # noqa: S608
    if count:
        raise RuntimeError(
            f"Refusing downgrade: {TABLE} holds {count} observation(s) of collected analytics."
        )
    for sid in (SYNC_SCHEDULE_ID, PURGE_SCHEDULE_ID):
        bind.execute(sa.text("DELETE FROM schedule_definitions WHERE id = :id"), {"id": sid})
    op.execute(sa.text("COMMENT ON TABLE metrics_snapshots IS NULL"))
    op.drop_constraint(
        op.f("ck_publish_schedules_publish_schedule_metrics_status_valid"),
        "publish_schedules",
        type_="check",
    )
    for column in ("metrics_detail", "metrics_status", "metrics_checked_at"):
        op.drop_column("publish_schedules", column)
    op.drop_index("ix_post_metric_obs_brand_observed", table_name=TABLE)
    op.drop_table(TABLE)
