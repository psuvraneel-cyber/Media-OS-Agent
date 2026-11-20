"""Weekly insight report on Telegram, Mondays 09:00 IST.

Revision ID: 0017_weekly_insights
Revises: 0016_media_cleanup
Create Date: 2026-11-21

Seeds `learning.weekly_report` (enabled). Each run sends every active brand that has published
posts a Telegram summary from the learning loop: the week's best and weakest post, what to do more
of and what to stop (only for groups with enough posts), early signals, prediction accuracy and
best posting times.

The schedule kind is `daily_time`; the handler itself only sends on Mondays (IST), so no new
schedule kind is needed.

No tables change. Downgrade removes the schedule.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_weekly_insights"
down_revision: str | Sequence[str] | None = "0016_media_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")
WEEKLY_ID = uuid.uuid5(_NS, "learning.weekly_report")

# Next occurrence of :hour::minute IST. Times are bound parameters; the SQL is fixed text.
_INSERT_SCHEDULE = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task, "
    "'NOTIFY', CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', "
    "(CASE WHEN (now() AT TIME ZONE 'Asia/Kolkata') < date_trunc('day', now() AT TIME "
    "ZONE 'Asia/Kolkata') + make_interval(hours => :hour, mins => :minute) THEN "
    "date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + make_interval(hours => :hour, "
    "mins => :minute) ELSE date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + "
    "interval '1 day' + make_interval(hours => :hour, mins => :minute) END) AT TIME ZONE "
    "'Asia/Kolkata', true, now(), now()) ON CONFLICT DO NOTHING"
)


def upgrade() -> None:
    op.execute(
        sa.text(_INSERT_SCHEDULE).bindparams(
            id=WEEKLY_ID,
            name="learning.weekly_report",
            task="media_os.learning.weekly_report",
            config=json.dumps({"kind": "daily_time", "hour": 9, "minute": 0}),
            payload=json.dumps({"trigger": "schedule"}),
            hour=9,
            minute=0,
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM schedule_definitions WHERE id = :id").bindparams(id=WEEKLY_ID))
