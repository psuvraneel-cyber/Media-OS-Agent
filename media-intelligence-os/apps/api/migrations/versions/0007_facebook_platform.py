"""Allow FACEBOOK as a channel and publish-schedule platform (roadmap N3).

Revision ID: 0007_facebook_platform
Revises: 0006_content_tags_qa_score
Create Date: 2026-11-15

The owner connected the Nimontron Facebook Page alongside Instagram in Postiz, and approved
carousels are cross-posted to both. `channel_accounts.platform` and `publish_schedules.platform`
only allowed INSTAGRAM and YOUTUBE.

The two old CHECK constraints carried double-prefixed names from 0001
(`ck_channel_accounts_ck_channel_accounts_channel_platform_valid`, and a truncated
`ck_publish_schedules_ck_publish_schedules_publish_sched_b2a0`); they are replaced with clean names.
Also seeds the global schedule `publish.sync_status` (every 15 min, reads post states from the
local Postiz) with `is_enabled = false`; the owner enables it.
Downgrade refuses while any FACEBOOK rows exist.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_facebook_platform"
down_revision: str | Sequence[str] | None = "0006_content_tags_qa_score"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = {
    "channel_accounts": "ck_channel_accounts_ck_channel_accounts_channel_platform_valid",
    "publish_schedules": "ck_publish_schedules_ck_publish_schedules_publish_sched_b2a0",
}
_NEW = {
    "channel_accounts": "ck_channel_accounts_channel_platform_valid",
    "publish_schedules": "ck_publish_schedules_publish_schedule_platform_valid",
}


SYNC_SCHEDULE_ID = uuid.uuid5(
    uuid.UUID("7c0f1d8e-2a51-4f0e-9d0e-5f3b2c1a9e47"), "publish.sync_status"
)


def _drop_check(table: str, names: list[str]) -> None:
    for name in names:
        op.execute(sa.text(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{name}"'))


def upgrade() -> None:
    for table in ("channel_accounts", "publish_schedules"):
        _drop_check(table, [_OLD[table], _NEW[table]])
        op.create_check_constraint(
            op.f(_NEW[table]), table, "platform IN ('INSTAGRAM', 'FACEBOOK', 'YOUTUBE')"
        )
    op.execute(
        sa.text(
            "INSERT INTO schedule_definitions (id, brand_profile_id, channel_account_id, name, "
            "task_name, task_type, schedule_config, payload_template, timezone, next_run_at, "
            "is_enabled, created_at, updated_at) VALUES (:id, NULL, NULL, 'publish.sync_status', "
            "'media_os.publish.sync_status', 'PUBLISH_SYNC', CAST(:config AS jsonb), "
            "CAST(:payload AS jsonb), 'Asia/Kolkata', now(), false, now(), now()) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(
            id=SYNC_SCHEDULE_ID,
            config=json.dumps({"kind": "interval", "seconds": 900}),
            payload=json.dumps({"trigger": "schedule"}),
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("channel_accounts", "publish_schedules"):
        count = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE platform = 'FACEBOOK'")  # noqa: S608
        ).scalar_one()
        if count:
            raise RuntimeError(
                f"Refusing downgrade: {table} has {count} FACEBOOK row(s). Remove them first."
            )
    bind.execute(
        sa.text("DELETE FROM schedule_definitions WHERE id = :id"), {"id": SYNC_SCHEDULE_ID}
    )
    for table in ("channel_accounts", "publish_schedules"):
        _drop_check(table, [_NEW[table]])
        op.create_check_constraint(op.f(_NEW[table]), table, "platform IN ('INSTAGRAM', 'YOUTUBE')")
