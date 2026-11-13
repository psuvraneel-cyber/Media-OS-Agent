"""Brand onboarding: validate a brand profile spec, show a diff, and apply it only on request.

Usage (inside the API container; the spec is read from stdin so it need not be in the image):
    docker exec -i media-os-api python -m media_os_api.brand_onboarding --brand brand-app-promo - \
        < docs/brands/nimontron/brand_profile.json             # dry run: prints the diff
    docker exec -i media-os-api python -m media_os_api.brand_onboarding --brand brand-app-promo \
        --apply - < docs/brands/nimontron/brand_profile.json   # writes + AuditEvent

The slug, id and activation state are never changed here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from media_os_api.db.models import BrandProfile
from media_os_api.db.normalization import compute_record_hash
from media_os_api.services.audit_service import record_audit_event
from media_os_api.services.brand_service import get_brand

_PILLAR = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PLAY_URL = re.compile(r"^https://play\.google\.com/store/apps/details\?id=[A-Za-z0-9._]+$")
FIELDS = (
    "name",
    "description",
    "niche",
    "target_audience",
    "language",
    "tone_of_voice",
    "content_pillars",
    "timezone",
    "visual_identity",
    "caption_preferences",
    "hashtag_preferences",
    "call_to_action_preferences",
    "publishing_preferences",
)


class BrandOnboardingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    niche: str = Field(min_length=1, max_length=1000)
    target_audience: str = Field(min_length=1, max_length=1000)
    language: str = Field(min_length=2, max_length=32)
    tone_of_voice: str = Field(min_length=1, max_length=1000)
    content_pillars: list[str] = Field(min_length=1, max_length=12)
    timezone: str
    visual_identity: dict[str, Any]
    caption_preferences: dict[str, Any]
    hashtag_preferences: dict[str, Any]
    call_to_action_preferences: dict[str, Any]
    publishing_preferences: dict[str, Any]

    @field_validator("content_pillars")
    @classmethod
    def _pillars(cls, value: list[str]) -> list[str]:
        bad = [p for p in value if not _PILLAR.match(p)]
        if bad or len(set(value)) != len(value):
            raise ValueError(f"Pillar codes must be unique snake_case identifiers: {bad}")
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone {value!r}") from exc
        return value

    @field_validator("call_to_action_preferences")
    @classmethod
    def _cta(cls, value: dict[str, Any]) -> dict[str, Any]:
        url = value.get("play_store_url")
        if url is not None and not (isinstance(url, str) and _PLAY_URL.match(url)):
            raise ValueError("play_store_url must be null or a play.google.com details URL.")
        return value

    @field_validator("publishing_preferences")
    @classmethod
    def _publishing(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("approval_required") is not True:
            raise ValueError(
                "approval_required must stay true (ADR-009) until the owner changes it."
            )
        for key in ("daily_caps", "weekly_caps"):
            caps = value.get(key, {})
            if not isinstance(caps, dict) or not all(
                isinstance(v, int) and 0 <= v <= 10 for v in caps.values()
            ):
                raise ValueError(f"{key} must map channels to integers between 0 and 10.")
        return value


def _current(brand: BrandProfile) -> dict[str, Any]:
    return {f: getattr(brand, f) for f in FIELDS}


def diff(brand: BrandProfile, spec: BrandOnboardingSpec) -> dict[str, dict[str, Any]]:
    """Fields whose value would change: {field: {"before": ..., "after": ...}}."""
    target = spec.model_dump()
    current = _current(brand)
    return {
        f: {"before": current[f], "after": target[f]} for f in FIELDS if current[f] != target[f]
    }


def apply_spec(
    session: Session, brand_ref: str, spec: BrandOnboardingSpec, *, actor_id: str, now: datetime
) -> dict[str, dict[str, Any]]:
    brand = get_brand(session, brand_ref)
    changes = diff(brand, spec)
    if not changes:
        return changes
    before_hash = compute_record_hash(_current(brand))
    for field, change in changes.items():
        setattr(brand, field, change["after"])
    session.flush()
    record_audit_event(
        session,
        actor_id=actor_id,
        action="BRAND_PROFILE_ONBOARDED",
        target_type="BrandProfile",
        target_id=str(brand.id),
        outcome="SUCCESS",
        before_hash=before_hash,
        after_hash=compute_record_hash(_current(brand)),
        metadata={"fields_changed": sorted(changes)},
        now=now,
    )
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--brand", required=True, help="Brand slug or id.")
    parser.add_argument(
        "--apply", action="store_true", help="Write the changes (default: dry run)."
    )
    parser.add_argument("spec", help="Path to the JSON spec, or '-' for stdin.")
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.spec == "-" else open(args.spec, encoding="utf-8").read()  # noqa: SIM115
    try:
        spec = BrandOnboardingSpec.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        print(f"[INVALID SPEC] {exc}", file=sys.stderr)
        return 2

    from media_os_api.config import load_settings
    from media_os_api.db.session import (
        create_db_engine_from_settings,
        create_session_factory,
        session_scope,
    )

    engine = create_db_engine_from_settings(load_settings())
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            if args.apply:
                changes = apply_spec(
                    db, args.brand, spec, actor_id="cli:brand_onboarding", now=datetime.now(UTC)
                )
            else:
                changes = diff(get_brand(db, args.brand), spec)
    finally:
        engine.dispose()

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written)"
    print(
        json.dumps(
            {"mode": mode, "fields_changed": sorted(changes), "changes": changes},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
