"""Design templates: the owner's own carousel backgrounds (cover, content, closing slide).

Revision ID: 0015_design_templates
Revises: 0014_autopilot
Create Date: 2026-11-17

`design_templates` holds a named set per brand. `slides` maps COVER / CONTENT / CTA to the
uploaded background (an Asset of type TEMPLATE_IMAGE), the text area (top / middle / bottom) and
the text tone (light / dark). Generation can use a set, the built-in design, or let Media OS
choose among sets that allow it (`auto_allowed`), preferring ones tagged for the occasion.

Downgrade drops the table (uploaded images stay as assets).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_design_templates"
down_revision: str | Sequence[str] | None = "0014_autopilot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "design_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "brand_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brand_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "occasions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "slides", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("jsonb_typeof(occasions) = 'array'", name="design_occasions_is_array"),
        sa.CheckConstraint("jsonb_typeof(slides) = 'object'", name="design_slides_is_object"),
    )
    op.create_index("ix_design_templates_brand", "design_templates", ["brand_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_design_templates_brand", table_name="design_templates")
    op.drop_table("design_templates")
