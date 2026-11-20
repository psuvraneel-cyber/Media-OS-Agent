"""Daily clean-up of published media on GitHub Pages at 03:30 IST.

Revision ID: 0016_media_cleanup
Revises: 0015_design_templates
Create Date: 2026-11-20

Seeds `publish.cleanup_media` (daily 03:30 IST, enabled). Each run deletes, in one commit, the
hosted slides and reels of posts that settled at least 3 days ago (15 when a platform failed),
so the public media repo stops growing without limit. Instagram and Facebook keep their own
copies; rescheduling a post uploads its files again.

No tables change. Downgrade removes the schedule.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_media_cleanup"
down_revision: str | Sequence[str] | None = "0015_design_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")
CLEANUP_ID = uuid.uuid5(_NS, "publish.cleanup_media")

# Next occurrence of :hour::minute IST. Times are bound parameters; the SQL is fixed text.
_INSERT_SCHEDULE = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task, "
    "'PUBLISH', CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', "
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
            id=CLEANUP_ID,
            name="publish.cleanup_media",
            task="media_os.publish.cleanup_media",
            config=json.dumps({"kind": "daily_time", "hour": 3, "minute": 30}),
            payload=json.dumps({"trigger": "schedule"}),
            hour=3,
            minute=30,
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM schedule_definitions WHERE id = :id").bindparams(id=CLEANUP_ID))
