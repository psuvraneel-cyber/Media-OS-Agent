"""Telegram bot commands: answer /status, /queue and /help from the owner's chat every minute.

Revision ID: 0011_telegram_commands
Revises: 0010_nightly_report
Create Date: 2026-11-16

Seeds the global schedule `notify.telegram_commands` (every 60 s, enabled). The job only reads
messages sent to the bot and replies to the owner's chat (TELEGRAM_CHAT_ID); it skips quietly when
Telegram isn't configured. It never changes content or schedules.

No tables or columns change. Downgrade removes the schedule.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_telegram_commands"
down_revision: str | Sequence[str] | None = "0010_nightly_report"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMANDS_ID = uuid.uuid5(
    uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47"), "notify.telegram_commands"
)

_INSERT = (
    "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
    "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
    "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, 'notify.telegram_commands', "
    "'media_os.notify.telegram_commands', 'NOTIFY', CAST(:config AS jsonb), "
    "CAST(:payload AS jsonb), 'Asia/Kolkata', now() + interval '1 minute', true, now(), now()) "
    "ON CONFLICT DO NOTHING"
)


def upgrade() -> None:
    op.execute(
        sa.text(_INSERT).bindparams(
            id=COMMANDS_ID,
            config=json.dumps({"kind": "interval", "seconds": 60}),
            payload=json.dumps({"trigger": "schedule"}),
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id = :id").bindparams(id=COMMANDS_ID)
    )
