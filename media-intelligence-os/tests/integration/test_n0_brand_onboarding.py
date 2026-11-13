"""Integration (disposable DB): brand onboarding dry run, apply, audit, idempotency, isolation."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.brand_onboarding import FIELDS, BrandOnboardingSpec, apply_spec, diff
from media_os_api.db import (
    AuditEvent,
    BrandProfile,
    create_db_engine,
    create_session_factory,
    session_scope,
)

pytestmark = pytest.mark.integration

SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "brands" / "nimontron" / "brand_profile.json"
)


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_db_engine(_db_url(), pool_size=2, max_overflow=1)
    factory = create_session_factory(engine)
    with session_scope(factory) as db:
        snapshot = {
            b.slug: {f: getattr(b, f) for f in FIELDS}
            for b in db.execute(select(BrandProfile)).scalars()
        }
    try:
        yield factory
    finally:
        with session_scope(factory) as db:  # restore seeded brands for other test modules
            for brand in db.execute(select(BrandProfile)).scalars():
                for f, v in snapshot[brand.slug].items():
                    setattr(brand, f, v)
        engine.dispose()


def _spec() -> BrandOnboardingSpec:
    data: dict[str, Any] = json.loads(SPEC_PATH.read_text("utf-8"))
    return BrandOnboardingSpec.model_validate(data)


def test_dry_run_apply_audit_idempotency_and_isolation(factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        tech_before = {
            f: getattr(
                db.execute(
                    select(BrandProfile).where(BrandProfile.slug == "brand-tech-ai")
                ).scalar_one(),
                f,
            )
            for f in FIELDS
        }
        promo = db.execute(
            select(BrandProfile).where(BrandProfile.slug == "brand-app-promo")
        ).scalar_one()
        planned = diff(promo, _spec())
        assert "content_pillars" in planned and "name" in planned
        assert promo.name != "Nimontron (নিমন্ত্রণ)"  # dry run wrote nothing

    with session_scope(factory) as db:
        changes = apply_spec(db, "brand-app-promo", _spec(), actor_id="test:n0", now=now)
        assert set(changes) == set(planned)

    with session_scope(factory) as db:
        promo = db.execute(
            select(BrandProfile).where(BrandProfile.slug == "brand-app-promo")
        ).scalar_one()
        assert promo.name == "Nimontron (নিমন্ত্রণ)" and promo.slug == "brand-app-promo"
        assert promo.content_pillars[0] == "wedding_season"
        assert promo.publishing_preferences["approval_required"] is True
        assert promo.is_active is True
        event = db.execute(
            select(AuditEvent).where(
                AuditEvent.action == "BRAND_PROFILE_ONBOARDED", AuditEvent.timestamp >= now
            )
        ).scalar_one()
        assert event.target_id == str(promo.id) and event.before_hash != event.after_hash
        tech = db.execute(
            select(BrandProfile).where(BrandProfile.slug == "brand-tech-ai")
        ).scalar_one()
        assert {f: getattr(tech, f) for f in FIELDS} == tech_before  # other brand untouched

    with session_scope(factory) as db:
        assert apply_spec(db, "brand-app-promo", _spec(), actor_id="test:n0", now=now) == {}
