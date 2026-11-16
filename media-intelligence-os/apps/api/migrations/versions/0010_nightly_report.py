"""Nightly report: one 22:00 IST Telegram report with the last 7 days of platform numbers.

Revision ID: 0010_nightly_report
Revises: 0009_owner_alerts
Create Date: 2026-11-16

* Moves `notify.daily_digest` from 21:00 to 22:00 IST. The report now also carries the last 7 days
  of views, likes and reactions, comments, shares, saves and reach (vs the 7 days before).
* Seeds `analytics.sync_post_metrics.nightly` at 21:40 IST (enabled), a run of the existing metrics
  sync with `force_recent: true`. It re-reads every post up to 30 days old so the report isn't up to
  6 hours stale; readings are stored per hour and upserted, so it never double-counts.

No tables or columns change. Downgrade restores 21:00 and removes the nightly sync.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_nightly_report"
down_revision: str | Sequence[str] | None = "0009_owner_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")
NIGHTLY_SYNC_ID = uuid.uuid5(_NS, "analytics.sync_post_metrics.nightly")


# Next occurrence of :hour::minute IST (today if still ahead, otherwise tomorrow). Times are bound
# parameters; the SQL is fixed text.
_MOVE_DIGEST = (
    "UPDATE schedule_definitions SET schedule_config = CAST(:config AS jsonb), "
    "next_run_at = (CASE WHEN (now() AT TIME ZONE 'Asia/Kolkata') < date_trunc('day', "
    "now() AT TIME ZONE 'Asia/Kolkata') + make_interval(hours => :hour, mins => :minute) "
    "THEN date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + make_interval(hours => "
    ":hour, mins => :minute) ELSE date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + "
    "interval '1 day' + make_interval(hours => :hour, mins => :minute) END) AT TIME ZONE "
    "'Asia/Kolkata', updated_at = now() WHERE name = 'notify.daily_digest' AND "
    "brand_profile_id IS NULL"
)

_INSERT_NIGHTLY_SYNC = (
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


def _move_digest(hour: int) -> None:
    op.execute(
        sa.text(_MOVE_DIGEST).bindparams(
            config=json.dumps({"kind": "daily_time", "hour": hour, "minute": 0}),
            hour=hour,
            minute=0,
        )
    )


def upgrade() -> None:
    _move_digest(22)
    op.execute(
        sa.text(_INSERT_NIGHTLY_SYNC).bindparams(
            id=NIGHTLY_SYNC_ID,
            name="analytics.sync_post_metrics.nightly",
            task="media_os.analytics.sync_post_metrics",
            config=json.dumps({"kind": "daily_time", "hour": 21, "minute": 40}),
            payload=json.dumps({"trigger": "schedule", "force_recent": True}),
            hour=21,
            minute=40,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id = :id").bindparams(id=NIGHTLY_SYNC_ID)
    )
    _move_digest(21)
