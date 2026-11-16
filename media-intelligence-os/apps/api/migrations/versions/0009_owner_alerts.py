"""Owner alerts: publishing watchdog and daily digest schedules.

Revision ID: 0009_owner_alerts
Revises: 0008_post_metric_observations
Create Date: 2026-11-16

Seeds two global schedules (enabled; both only read state and queue Telegram alerts, and they
skip quietly when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set):

* `notify.watchdog` every 5 min: posts still queued in Postiz 10 min after their time, Postiz not
  answering, disabled channels, and the 15-min status sync having stopped;
* `notify.daily_digest` at 21:00 IST: what was posted, what failed, what's next.

Also enables `publish.sync_status` (seeded disabled in 0007; the owner turned it on by hand). The
watchdog and every published/failed alert depend on it, so it must not be off by accident.

No tables or columns change. Downgrade removes the two schedules and leaves the sync as it is.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_owner_alerts"
down_revision: str | Sequence[str] | None = "0008_post_metric_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NS = uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47")
WATCHDOG_ID = uuid.uuid5(_NS, "notify.watchdog")
DIGEST_ID = uuid.uuid5(_NS, "notify.daily_digest")

_INSERT = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, :name, :task, 'NOTIFY', "
    "CAST(:config AS jsonb), CAST(:payload AS jsonb), 'Asia/Kolkata', {next_run}, true, now(), "
    "now()) ON CONFLICT DO NOTHING"
)


def upgrade() -> None:
    op.execute(
        sa.text(_INSERT.format(next_run="now() + interval '5 minutes'")).bindparams(
            id=WATCHDOG_ID,
            name="notify.watchdog",
            task="media_os.notify.watchdog",
            config=json.dumps({"kind": "interval", "seconds": 300}),
            payload=json.dumps({"trigger": "schedule"}),
        )
    )
    op.execute(
        sa.text(
            _INSERT.format(
                # The next 21:00 IST (today if it's still before 21:00, otherwise tomorrow).
                next_run=(
                    "(CASE WHEN (now() AT TIME ZONE 'Asia/Kolkata')::time < time '21:00' "
                    "THEN date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') "
                    "+ interval '21 hours' "
                    "ELSE date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') "
                    "+ interval '1 day 21 hours' END) AT TIME ZONE 'Asia/Kolkata'"
                )
            )
        ).bindparams(
            id=DIGEST_ID,
            name="notify.daily_digest",
            task="media_os.notify.daily_digest",
            config=json.dumps({"kind": "daily_time", "hour": 21, "minute": 0}),
            payload=json.dumps({"trigger": "schedule"}),
        )
    )
    op.execute(
        sa.text(
            "UPDATE schedule_definitions SET is_enabled = true, "
            "next_run_at = COALESCE(next_run_at, now()), updated_at = now() "
            "WHERE name = 'publish.sync_status' AND brand_profile_id IS NULL AND NOT is_enabled"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id IN (:a, :b)").bindparams(
            a=WATCHDOG_ID, b=DIGEST_ID
        )
    )
