"""Harden claim-source protection, brand-logo asset isolation, and content status checks.

Revision ID: 0003_domain_integrity_hardening
Revises: 0002_seed_initial_brands
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_domain_integrity_hardening"
down_revision: str | Sequence[str] | None = "0002_seed_initial_brands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALID_STATUSES_SQL = (
    "('DISCOVERED', 'NORMALIZED', 'DEDUPED', 'RANKED', 'RESEARCHED', "
    "'DRAFTED', 'RENDERED', 'QA_PASSED', 'POSTIZ_DRAFT', 'USER_APPROVED', "
    "'SCHEDULED', 'PUBLISHING', 'PUBLISHED', 'METRICS_CAPTURED', 'ARCHIVED', "
    "'FAILED_RETRYABLE', 'FAILED_FINAL', 'REJECTED', 'PAUSED')"
)


def upgrade() -> None:
    op.create_check_constraint(
        "ck_content_packages_content_status_valid",
        "content_packages",
        f"status IN {_VALID_STATUSES_SQL}",
    )
    op.create_check_constraint(
        "ck_content_packages_content_previous_status_valid",
        "content_packages",
        f"previous_status IS NULL OR previous_status IN {_VALID_STATUSES_SQL}",
    )
    op.create_check_constraint(
        "ck_content_state_transitions_transition_to_status_valid",
        "content_state_transitions",
        f"to_status IN {_VALID_STATUSES_SQL}",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION media_os_prevent_verified_claim_source_loss()
        RETURNS TRIGGER AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM claims
                WHERE id = OLD.claim_id AND verification_status = 'VERIFIED'
            ) THEN
                IF NOT EXISTS (
                    SELECT 1 FROM claim_sources WHERE claim_id = OLD.claim_id
                ) THEN
                    RAISE EXCEPTION
                        'Cannot remove the last ClaimSource from VERIFIED Claim %.',
                        OLD.claim_id
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;

        CREATE CONSTRAINT TRIGGER trg_claim_sources_protect_verified_claim
        AFTER DELETE OR UPDATE OF claim_id ON claim_sources
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION media_os_prevent_verified_claim_source_loss();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION media_os_protect_brand_logo_asset()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.brand_profile_id IS DISTINCT FROM NEW.brand_profile_id THEN
                IF EXISTS (
                    SELECT 1
                    FROM brand_profiles
                    WHERE logo_asset_id = OLD.id
                      AND id IS DISTINCT FROM NEW.brand_profile_id
                ) THEN
                    RAISE EXCEPTION
                        'Asset % is assigned as logo_asset_id for another BrandProfile.',
                        OLD.id
                        USING ERRCODE = '23503';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_assets_protect_brand_logo
        BEFORE UPDATE OF brand_profile_id ON assets
        FOR EACH ROW
        EXECUTE FUNCTION media_os_protect_brand_logo_asset();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_assets_protect_brand_logo ON assets;")
    op.execute("DROP FUNCTION IF EXISTS media_os_protect_brand_logo_asset();")
    op.execute("DROP TRIGGER IF EXISTS trg_claim_sources_protect_verified_claim ON claim_sources;")
    op.execute("DROP FUNCTION IF EXISTS media_os_prevent_verified_claim_source_loss();")

    op.drop_constraint(
        "ck_content_state_transitions_transition_to_status_valid",
        "content_state_transitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_content_packages_content_previous_status_valid",
        "content_packages",
        type_="check",
    )
    op.drop_constraint(
        "ck_content_packages_content_status_valid",
        "content_packages",
        type_="check",
    )
