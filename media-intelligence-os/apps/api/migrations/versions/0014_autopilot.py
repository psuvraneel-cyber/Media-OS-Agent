"""Autopilot: daily drafts at 08:00 IST.

Revision ID: 0014_autopilot
Revises: 0013_ops_requests
Create Date: 2026-11-17

Seeds `content.daily_drafts` (daily 08:00 IST, enabled). Each run queues every active brand's
daily drafts (default 1, set in the dashboard; 0 turns it off) and, when auto-scheduling is on,
puts approved Media OS carousels into free posting slots. Nothing is scheduled without approval.

No tables change. Downgrade removes the schedule.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_autopilot"
down_revision: str | Sequence[str] | None = "0013_ops_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")
DAILY_DRAFTS_ID = uuid.uuid5(_NS, "content.daily_drafts")

# Next occurrence of :hour::minute IST. Times are bound parameters; the SQL is fixed text.
_INSERT_SCHEDULE = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task, "
    "'CONTENT_GENERATION', CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', "
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
            id=DAILY_DRAFTS_ID,
            name="content.daily_drafts",
            task="media_os.content.daily_drafts",
            config=json.dumps({"kind": "daily_time", "hour": 8, "minute": 0}),
            payload=json.dumps({"trigger": "schedule"}),
            hour=8,
            minute=0,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id = :id").bindparams(id=DAILY_DRAFTS_ID)
    )
