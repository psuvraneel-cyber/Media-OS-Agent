"""Integration (disposable DB): each AI attempt lands in `model_usages` at zero cost."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.ai.config import build_router
from media_os_api.ai.quota import InMemoryQuotaStore
from media_os_api.ai.types import GenerationRequest, TaskClass
from media_os_api.ai.usage import usage_recorder
from media_os_api.db import (
    BrandProfile,
    ModelUsage,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.research.http.ratelimit import InMemoryMinIntervalLimiter

pytestmark = pytest.mark.integration

ROUTES = '{"BENGALI_COPY": [["gemini", "gemini-2.5-flash"], ["groq", "llama-3.3-70b-versatile"]]}'
SECRET_PROMPT = "unique-prompt-marker-7f3a"


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_db_engine(_db_url(), pool_size=2, max_overflow=1)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        with session_scope(factory) as db:
            db.execute(delete(ModelUsage).where(ModelUsage.project_alias == "free-tier"))
        engine.dispose()


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "generativelanguage.googleapis.com":
        return httpx.Response(429)
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "শুভ দীপাবলি"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3},
        },
    )


def test_attempts_recorded_per_provider(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as db:
        brand_id: uuid.UUID = db.execute(
            select(BrandProfile.id).where(BrandProfile.slug == "brand-app-promo")
        ).scalar_one()

    router = build_router(
        quota_store=InMemoryQuotaStore(),
        environ={"GEMINI_API_KEY": "k1", "GROQ_API_KEY": "k2", "AI_ROUTES": ROUTES},
        rate_limiter=InMemoryMinIntervalLimiter(sleep=lambda _s: None),
        transport=httpx.MockTransport(_handler),
        resolver=lambda _h, _p: ["142.250.1.1"],
        on_attempt=usage_recorder(factory, brand_profile_id=brand_id),
        sleep=lambda _s: None,
    )
    result = router.generate(
        GenerationRequest(
            task=TaskClass.BENGALI_COPY, system="s", prompt=SECRET_PROMPT, operation="caption"
        )
    )
    assert result.provider == "groq"

    with session_scope(factory) as db:
        rows = (
            db.execute(
                select(ModelUsage)
                .where(ModelUsage.project_alias == "free-tier")
                .order_by(ModelUsage.created_at)
            )
            .scalars()
            .all()
        )
        got = [
            (r.provider, r.request_status, r.quota_status, r.input_tokens, r.output_tokens)
            for r in rows
        ]
        assert got == [("gemini", "QUOTA", "LIMITED", 0, 0), ("groq", "OK", "OK", 9, 3)]
        for r in rows:
            assert r.brand_profile_id == brand_id
            assert r.estimated_cost == Decimal("0")
            assert r.operation == "BENGALI_COPY:caption"
            values = " ".join(str(getattr(r, c.key)) for c in ModelUsage.__table__.columns)
            assert SECRET_PROMPT not in values and "k1" not in values.split()
