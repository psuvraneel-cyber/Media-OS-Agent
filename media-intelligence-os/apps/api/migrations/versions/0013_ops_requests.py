"""Owner-requested host actions: a "Restart Postiz" button on the dashboard.

Revision ID: 0013_ops_requests
Revises: 0012_account_metrics
Create Date: 2026-11-17

The API never touches Docker. The dashboard records a request in `ops_requests`; the Windows
helper task (`infra/ops/windows/ops-requests.ps1`, every minute) picks it up, restarts the
postiz-runtime-app container and records the outcome. Only the one allow-listed action exists.

Downgrade drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_ops_requests"
down_revision: str | Sequence[str] | None = "0012_account_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_detail", sa.Text(), nullable=True),
        sa.CheckConstraint("action IN ('RESTART_POSTIZ')", name="ops_request_action_valid"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'EXPIRED')",
            name="ops_request_status_valid",
        ),
    )
    op.create_index("ix_ops_requests_status_requested", "ops_requests", ["status", "requested_at"])


def downgrade() -> None:
    op.drop_index("ix_ops_requests_status_requested", table_name="ops_requests")
    op.drop_table("ops_requests")
