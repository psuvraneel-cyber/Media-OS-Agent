"""Idempotent seed migration for Technology & AI and App Promotion brand profiles.

Revision ID: 0002_seed_initial_brands
Revises: 0001_initial_phase4_schema
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from media_os_api.db.seed import INITIAL_BRAND_SPECS, seed_initial_brands

revision: str = "0002_seed_initial_brands"
down_revision: str | Sequence[str] | None = "0001_initial_phase4_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        seed_initial_brands(session)
        session.flush()
    finally:
        session.close()


def downgrade() -> None:
    bind = op.get_bind()
    slugs = [spec["slug"] for spec in INITIAL_BRAND_SPECS]
    bind.execute(
        sa.text("DELETE FROM brand_profiles WHERE slug = ANY(:slugs)"),
        {"slugs": slugs},
    )
