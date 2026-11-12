"""Controlled idempotent database seed and single-owner first-boot provisioning."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from media_os_api.config import Settings
from media_os_api.db.models import AuditEvent, BrandProfile, OwnerUser
from media_os_api.db.normalization import compute_record_hash

BRAND_TECH_AI_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "media-os.brand-tech-ai")
BRAND_APP_PROMO_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "media-os.brand-app-promo")

INITIAL_BRAND_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": BRAND_TECH_AI_ID,
        "name": "Technology & AI",
        "slug": "brand-tech-ai",
        "description": (
            "Primary technical editorial brand focused on primary-source verification, "
            "developer intelligence, open-source software, and software engineering."
        ),
        "niche": "AI and developer intelligence.",
        "target_audience": "Developers, AI enthusiasts and software engineers.",
        "language": "en",
        "tone_of_voice": "Technically credible, informative, concise and analytical.",
        "content_pillars": [
            "AI models and releases",
            "AI tools",
            "Open-source projects",
            "GitHub discoveries",
            "AI research",
            "Developer productivity",
            "Software engineering",
        ],
        "visual_identity": {"configured": True, "theme": "technical_dark"},
        "logo_asset_id": None,
        "caption_preferences": {"style": "evidence_first", "include_source_attribution": True},
        "hashtag_preferences": {"mode": "curated_technical", "max_tags": 8},
        "call_to_action_preferences": {"mode": "save_and_explore_source"},
        "timezone": "Asia/Kolkata",
        "publishing_preferences": {
            "approval_policy": "human_required",
            "placeholder_unconfigured": False,
            "instagram_mode": "draft_review",
            "youtube_mode": "explicit_human_approval",
        },
        "is_active": True,
    },
    {
        "id": BRAND_APP_PROMO_ID,
        "name": "App Promotion",
        "slug": "brand-app-promo",
        "description": (
            "Product promotion and education profile for the owner's application. "
            "Application name, logo, color palette, features, screenshots, pricing, claims, "
            "and user statistics remain intentionally unconfigured until owner verification."
        ),
        "niche": "App promotion and product education.",
        "target_audience": "Provisional app users (unconfigured pending owner product setup).",
        "language": "en",
        "tone_of_voice": "Credible, clear and product-focused.",
        "content_pillars": [
            "Product education",
            "Feature walkthroughs",
            "Release updates",
            "User tutorials",
        ],
        "visual_identity": {"configured": False},
        "logo_asset_id": None,
        "caption_preferences": {"configured": False},
        "hashtag_preferences": {"configured": False},
        "call_to_action_preferences": {"configured": False},
        "timezone": "Asia/Kolkata",
        "publishing_preferences": {
            "approval_policy": "human_required",
            "placeholder_unconfigured": True,
            "language_provisional": True,
            "instagram_mode": "draft_review",
            "youtube_mode": "explicit_human_approval",
        },
        "is_active": True,
    },
)


@dataclass(frozen=True)
class SeedResult:
    """Outcome summary of idempotent brand seeding."""

    inserted_slugs: tuple[str, ...]
    total_brands: int


def seed_initial_brands(session: Session) -> SeedResult:
    """Idempotently ensure the two initial brand profiles exist without duplicating rows."""
    inserted: list[str] = []
    for spec in INITIAL_BRAND_SPECS:
        stmt = (
            pg_insert(BrandProfile)
            .values(**spec)
            .on_conflict_do_nothing(index_elements=["slug"])
            .returning(BrandProfile.slug)
        )
        res = session.execute(stmt).scalar_one_or_none()
        if res is not None:
            inserted.append(str(res))

    total = len(session.execute(select(BrandProfile.id)).scalars().all())
    return SeedResult(inserted_slugs=tuple(inserted), total_brands=total)


def ensure_owner_bootstrapped(session: Session, settings: Settings) -> tuple[OwnerUser, bool]:
    """Provision the single owner on first DB initialization only.

    Never silently overwrites an existing database owner's password on restarts.
    """
    existing = session.execute(select(OwnerUser).limit(1)).scalar_one_or_none()
    if existing is not None:
        return existing, False

    now = datetime.now(UTC)
    owner = OwnerUser(
        username=settings.owner_username,
        password_hash=settings.resolved_owner_password_hash(),
        is_active=True,
        token_version=1,
        password_changed_at=now,
    )
    session.add(owner)
    session.flush()

    session.add(
        AuditEvent(
            actor_id="system:bootstrap",
            action="OWNER_BOOTSTRAPPED",
            target_type="OwnerUser",
            target_id=str(owner.id),
            request_id="-",
            after_hash=compute_record_hash({"username": owner.username, "token_version": 1}),
            outcome="SUCCESS",
            event_metadata={"username": owner.username, "source": "initial_bootstrap"},
            retention_expires_at=now + timedelta(days=settings.retention_audit_event_days),
        )
    )
    return owner, True
