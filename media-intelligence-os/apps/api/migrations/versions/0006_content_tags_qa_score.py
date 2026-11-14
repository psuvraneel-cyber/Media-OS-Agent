"""Add post tags, QA report and score to content packages (roadmap N2).

Revision ID: 0006_content_tags_qa_score
Revises: 0005_research_source_registry
Create Date: 2026-11-14

Additive, owner-approved (2026-11-14). Three nullable JSONB columns on `content_packages`:

* `post_tags`  — the closed `PostTags` taxonomy set (validated in the application), needed by the
  N5 learning loop to compare hooks/formats/languages.
* `qa_report`  — automated QA result (checks, pass/fail, findings) for the review queue.
* `score`      — virality score v0 (rubric breakdown; explicitly a heuristic).

Each must be NULL or a JSON object. Existing rows are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_content_tags_qa_score"
down_revision: str | Sequence[str] | None = "0005_research_source_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("post_tags", "qa_report", "score")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column(
            "content_packages",
            sa.Column(column, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
        op.create_check_constraint(
            op.f(f"ck_content_packages_{column}_is_object"),
            "content_packages",
            f"{column} IS NULL OR jsonb_typeof({column}) = 'object'",
        )


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_constraint(
            op.f(f"ck_content_packages_{column}_is_object"), "content_packages", type_="check"
        )
        op.drop_column("content_packages", column)
