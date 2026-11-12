"""System module boundary descriptors and non-DB test fixture fallbacks."""

from __future__ import annotations

from typing import Literal

from media_os_api.config import Settings
from media_os_api.schemas.brands import BrandProfileSummary, ChannelAccountSummary
from media_os_api.schemas.system import ModuleBoundaryStatus

_BRAND_FIXTURES: tuple[BrandProfileSummary, ...] = (
    BrandProfileSummary(
        brand_id="brand-tech-ai",
        slug="brand-tech-ai",
        name="Technology & AI",
        niche="AI and developer intelligence.",
        description=(
            "Primary technical editorial brand focused on primary-source verification, "
            "developer tooling insights, and engineering explainers."
        ),
        target_audience="Developers, AI enthusiasts and software engineers.",
        language="en",
        tone_of_voice="Technically credible, informative, concise and analytical.",
        content_pillars=[
            "AI models and releases",
            "AI tools",
            "Open-source projects",
            "GitHub discoveries",
            "AI research",
            "Developer productivity",
            "Software engineering",
        ],
        visual_identity={"configured": True},
        timezone="Asia/Kolkata",
        approval_policy="human_required",
        placeholder_unconfigured=False,
        active=True,
        data_origin="phase3_prototype_fixture",
    ),
    BrandProfileSummary(
        brand_id="brand-app-promo",
        slug="brand-app-promo",
        name="App Promotion",
        niche="App promotion and product education.",
        description=(
            "Placeholder prototype profile for the owner's published application. "
            "App name, logo, brand palette, features, and marketing claims are intentionally "
            "unconfigured until separate owner setup."
        ),
        target_audience="Provisional app users (unconfigured).",
        language="en",
        tone_of_voice="Credible, clear and product-focused.",
        content_pillars=[
            "Product education",
            "Feature walkthroughs",
            "Release updates",
            "User tutorials",
        ],
        visual_identity={"configured": False},
        timezone="Asia/Kolkata",
        approval_policy="human_required",
        placeholder_unconfigured=True,
        active=True,
        data_origin="phase3_prototype_fixture",
    ),
)

_CHANNEL_FIXTURES: tuple[ChannelAccountSummary, ...] = ()


def list_brand_fixtures() -> list[BrandProfileSummary]:
    """Return immutable non-persistent fallback BrandProfile summaries (no-DB mode only)."""
    return list(_BRAND_FIXTURES)


def get_brand_fixture(brand_id: str) -> BrandProfileSummary | None:
    """Look up a non-persistent fallback BrandProfile by ID or slug."""
    for brand in _BRAND_FIXTURES:
        if brand.brand_id == brand_id or brand.slug == brand_id:
            return brand
    return None


def list_channel_fixtures(brand_id: str | None = None) -> list[ChannelAccountSummary]:
    """Return empty unconnected ChannelAccount list (zero fabricated channels)."""
    if brand_id is None:
        return list(_CHANNEL_FIXTURES)
    return [ch for ch in _CHANNEL_FIXTURES if ch.brand_id == brand_id]


def get_module_boundaries(
    settings: Settings,
    *,
    persistence_active: bool = False,
    db_ok: bool = False,
    redis_ok: bool = False,
) -> list[ModuleBoundaryStatus]:
    """Return honest operational status for each Media Intelligence OS subsystem."""
    reddit_status: Literal["not_implemented", "blocked_by_policy"] = (
        "blocked_by_policy"
        if settings.source_reddit_status.strip().upper() == "BLOCKED"
        else "not_implemented"
    )
    persistence_status: Literal["active_phase_4", "not_implemented"] = (
        "active_phase_4" if (persistence_active and db_ok and redis_ok) else "not_implemented"
    )
    return [
        ModuleBoundaryStatus(
            module_id="web-dashboard",
            title="Next.js Operator Dashboard",
            status="active_phase_4" if persistence_active else "active_phase_3",
            target_phase="phase-4" if persistence_active else "phase-3",
            summary="Authenticated App Router shell, PostgreSQL brand switcher, and 12 routes.",
        ),
        ModuleBoundaryStatus(
            module_id="fastapi-control-plane",
            title="FastAPI Backend & Auth Control Plane",
            status="active_phase_4" if persistence_active else "active_phase_3",
            target_phase="phase-4" if persistence_active else "phase-3",
            summary=(
                "PostgreSQL owner/session persistence, Redis rate limiting, CSRF, and workflow "
                "rules."
                if persistence_active
                else "Single-owner Argon2id auth, HttpOnly/CSRF cookies, and typed schemas."
            ),
        ),
        ModuleBoundaryStatus(
            module_id="postgres-redis-celery",
            title="PostgreSQL, Redis, Celery Worker & Scheduler",
            status=persistence_status,
            target_phase="phase-4",
            summary=(
                "Application PostgreSQL 17, Redis 7.2, Celery JSON worker (concurrency=1), "
                "transactional outbox, and persistent scheduler active."
                if persistence_status == "active_phase_4"
                else "PostgreSQL or Redis persistence is not currently active/healthy."
            ),
        ),
        ModuleBoundaryStatus(
            module_id="research-connectors",
            title="Research Discovery Connectors",
            status="not_implemented",
            target_phase="phase-5",
            summary="Deferred to Phase 5. Source ingestion pipelines and ranking are disabled.",
        ),
        ModuleBoundaryStatus(
            module_id="reddit-source-policy",
            title="Reddit Connector Policy Gate",
            status=reddit_status,
            target_phase="blocked",
            summary="Explicitly BLOCKED pending formal permitted-use and Data API Terms review.",
        ),
        ModuleBoundaryStatus(
            module_id="gemini-creative-tts",
            title="Gemini Developer API & FFmpeg Media Studio",
            status="not_implemented",
            target_phase="phase-7",
            summary="Deferred to Phase 7. AI generation, TTS, and carousel rendering disabled.",
        ),
        ModuleBoundaryStatus(
            module_id="postiz-publishing-bridge",
            title="Postiz Publishing & Social OAuth Workflows",
            status="not_implemented",
            target_phase="phase-8",
            summary="Publishing/OAuth deferred to Phase 8. Standalone Postiz is probed read-only.",
        ),
        ModuleBoundaryStatus(
            module_id="analytics-learning-loop",
            title="Analytics Ingestion & Feedback Loop",
            status="not_implemented",
            target_phase="phase-9",
            summary="Deferred to Phase 9. Metrics charts remain unpopulated placeholders.",
        ),
        ModuleBoundaryStatus(
            module_id="custom-mcp-server",
            title="Custom Media OS MCP Server",
            status="not_implemented",
            target_phase="phase-10",
            summary="Deferred to Phase 10.",
        ),
    ]
