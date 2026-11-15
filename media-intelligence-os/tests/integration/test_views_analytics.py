"""Integration (disposable DB): views analytics ingestion, idempotency, brand isolation, reports.

Postiz is an in-memory fake; observations and reports use real PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import delete, func, select

from media_os_api.analytics import service
from media_os_api.analytics.sync import handle_sync_post_metrics
from media_os_api.db import (
    BrandProfile,
    ChannelAccount,
    ContentPackage,
    JobRun,
    PostMetricObservation,
    PublishSchedule,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.errors import RetryableTaskError
from media_os_api.publish.postiz import PostizError
from media_os_api.publish.service import ClientFactory

pytestmark = pytest.mark.integration


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


class FakeHost:
    def close(self) -> None:
        return None


class FakePostiz:
    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def post_analytics(self, post_id: str) -> Any:
        self.calls.append(post_id)
        value = self.payloads.get(post_id, [])
        if isinstance(value, Exception):
            raise value
        return value

    def close(self) -> None:
        return None


def _no_sleep(_seconds: float) -> None:
    return None


def _force_due(world: dict[str, Any]) -> None:
    with session_scope(world["factory"]) as db:
        for slug in ("brand-app-promo", "brand-tech-ai"):
            for key in ("ig", "fb"):
                sched = db.get(PublishSchedule, world[slug][key])
                assert sched
                sched.metrics_checked_at = None


def _factory(postiz: FakePostiz) -> ClientFactory:
    """The handler only calls close() on the host and post_analytics() on Postiz."""
    return cast(ClientFactory, lambda: (FakeHost(), postiz))


def _metric(label: str, total: int) -> dict[str, Any]:
    return {"label": label, "data": [{"total": str(total), "date": "2026-11-15"}]}


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    engine = create_db_engine(_db_url(), pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    created: dict[str, list[uuid.UUID]] = {"sched": [], "pkg": [], "chan": []}
    now = datetime.now(UTC)
    with session_scope(factory) as db:
        brands = {
            b.slug: b
            for b in db.execute(
                select(BrandProfile).where(
                    BrandProfile.slug.in_(("brand-app-promo", "brand-tech-ai"))
                )
            ).scalars()
        }
        out: dict[str, Any] = {"factory": factory, "now": now}
        for slug, brand in brands.items():
            tag = uuid.uuid4().hex[:8]
            chans = {}
            for platform in ("INSTAGRAM", "FACEBOOK"):
                c = ChannelAccount(
                    brand_profile_id=brand.id,
                    platform=platform,
                    external_account_id=f"postiz:{tag}-{platform}",
                    external_display_name=f"{slug} {platform.title()}",
                    postiz_integration_id=f"{tag}-{platform}",
                    status="CONNECTED",
                )
                db.add(c)
                chans[platform] = c
            db.flush()
            created["chan"] += [c.id for c in chans.values()]

            def package(status: str, brand_id: uuid.UUID = brand.id) -> ContentPackage:
                p = ContentPackage(
                    brand_profile_id=brand_id,
                    platform="INSTAGRAM",
                    format="CAROUSEL",
                    status=status,
                    approval_status="APPROVED",
                    title=f"Post {uuid.uuid4().hex[:6]}",
                    post_tags={"hook_type": "PAIN_POINT", "language": "BN_EN", "pillar": "x"},
                )
                db.add(p)
                db.flush()
                created["pkg"].append(p.id)
                return p

            def schedule(
                pkg: ContentPackage,
                platform: str,
                status: str,
                pid: str | None,
                chans: dict[str, ChannelAccount] = chans,
            ) -> PublishSchedule:
                s = PublishSchedule(
                    brand_profile_id=pkg.brand_profile_id,
                    channel_account_id=chans[platform].id,
                    content_package_id=pkg.id,
                    platform=platform,
                    postiz_post_id=pid,
                    scheduled_at=now - timedelta(days=10),
                    status=status,
                    idempotency_key=f"it-{uuid.uuid4().hex}",
                )
                db.add(s)
                db.flush()
                created["sched"].append(s.id)
                return s

            published = package("PUBLISHED")
            ig = schedule(published, "INSTAGRAM", "PUBLISHED", f"{tag}-ig")
            fb = schedule(published, "FACEBOOK", "PUBLISHED", f"{tag}-fb")
            pending = package("SCHEDULED")
            schedule(pending, "INSTAGRAM", "SCHEDULED", f"{tag}-pending")
            failed = package("QA_PASSED")
            schedule(failed, "INSTAGRAM", "FAILED", f"{tag}-failed")
            out[slug] = {
                "brand_id": brand.id,
                "tag": tag,
                "pkg": published.id,
                "ig": ig.id,
                "fb": fb.id,
                "ig_chan": chans["INSTAGRAM"].id,
            }
    try:
        yield out
    finally:
        with session_scope(factory) as db:
            db.execute(
                delete(PostMetricObservation).where(
                    PostMetricObservation.publish_schedule_id.in_(created["sched"])
                )
            )
            db.execute(delete(PublishSchedule).where(PublishSchedule.id.in_(created["sched"])))
            db.execute(delete(ContentPackage).where(ContentPackage.id.in_(created["pkg"])))
            db.execute(delete(ChannelAccount).where(ChannelAccount.id.in_(created["chan"])))
        engine.dispose()


def _payloads(world: dict[str, Any], ig_views: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for slug in ("brand-app-promo", "brand-tech-ai"):
        tag = world[slug]["tag"]
        base = ig_views if slug == "brand-app-promo" else 7
        out[f"{tag}-ig"] = [_metric("Views", base), _metric("Likes", 5), _metric("Comments", 1)]
        out[f"{tag}-fb"] = [_metric("Impressions", 40), _metric("Reactions", 3)]
    return out


def _count(db: Any, schedule_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count()).where(PostMetricObservation.publish_schedule_id == schedule_id)
        ).scalar_one()
    )


def test_sync_only_reads_published_posts_and_is_idempotent(world: dict[str, Any]) -> None:
    fake = FakePostiz(_payloads(world, 120))
    with session_scope(world["factory"]) as db:
        result = handle_sync_post_metrics(db, JobRun(), clients=_factory(fake), sleep=_no_sleep)
    tag = world["brand-app-promo"]["tag"]
    assert f"{tag}-pending" not in fake.calls and f"{tag}-failed" not in fake.calls
    assert f"{tag}-ig" in fake.calls and f"{tag}-fb" in fake.calls
    assert result["observations"] >= 6
    # A run right after is skipped: nothing is due yet (call budget).
    idle = FakePostiz(_payloads(world, 999))
    with session_scope(world["factory"]) as db:
        again = handle_sync_post_metrics(db, JobRun(), clients=_factory(idle), sleep=_no_sleep)
    assert f"{tag}-ig" not in idle.calls and again["not_due"] >= 2
    # Forced re-read in the same hour with a newer value: same rows, updated value.
    _force_due(world)
    fake2 = FakePostiz(_payloads(world, 150))
    with session_scope(world["factory"]) as db:
        handle_sync_post_metrics(db, JobRun(), clients=_factory(fake2), sleep=_no_sleep)
    with session_scope(world["factory"]) as db:
        ig = world["brand-app-promo"]["ig"]
        assert _count(db, ig) == 3  # views, likes, comments — no duplicates
        views = db.execute(
            select(PostMetricObservation.value).where(
                PostMetricObservation.publish_schedule_id == ig,
                PostMetricObservation.metric_key == "views",
            )
        ).scalar_one()
        assert views == Decimal("150")
        fb_keys = set(
            db.execute(
                select(PostMetricObservation.metric_key).where(
                    PostMetricObservation.publish_schedule_id == world["brand-app-promo"]["fb"]
                )
            ).scalars()
        )
        assert fb_keys == {"unique_viewers", "reactions"}  # Facebook "Impressions" ≠ views
        ig_sched = db.get(PublishSchedule, ig)
        assert ig_sched and ig_sched.metrics_status == "OK" and ig_sched.metrics_checked_at


def test_no_data_and_missing_are_recorded_and_backed_off(world: dict[str, Any]) -> None:
    tag = world["brand-app-promo"]["tag"]
    payloads: dict[str, Any] = {f"{tag}-ig": [], f"{tag}-fb": None}  # [] = no data, None = missing
    with session_scope(world["factory"]) as db:
        handle_sync_post_metrics(
            db, JobRun(), clients=_factory(FakePostiz(payloads)), sleep=_no_sleep
        )
    with session_scope(world["factory"]) as db:
        ig = db.get(PublishSchedule, world["brand-app-promo"]["ig"])
        fb = db.get(PublishSchedule, world["brand-app-promo"]["fb"])
        assert ig and ig.metrics_status == "NO_DATA" and ig.metrics_detail
        assert fb and fb.metrics_status == "MISSING"
        report = service.views_report(db, world["brand-app-promo"]["brand_id"], use_cache=False)
        assert report["coverage"]["no_data_posts"] == 1
        assert report["coverage"]["missing_posts"] == 1
        rows = service.posts_report(db, world["brand-app-promo"]["brand_id"])["items"]
        assert {r["metrics_status"] for r in rows} == {"NO_DATA", "MISSING"}
    # Six hours later a NO_DATA post is not retried yet (daily back-off).
    later = datetime.now(UTC) + timedelta(hours=6)
    retry = FakePostiz(payloads)
    with session_scope(world["factory"]) as db:
        handle_sync_post_metrics(db, JobRun(), clients=_factory(retry), sleep=_no_sleep, now=later)
    assert f"{tag}-ig" not in retry.calls and f"{tag}-fb" not in retry.calls


def test_errors_are_not_marked_checked_and_run_is_capped(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_os_api.analytics import sync as sync_mod

    monkeypatch.setattr(sync_mod, "MAX_POSTS_PER_RUN", 1)
    tag = world["brand-app-promo"]["tag"]

    class OneBad(FakePostiz):
        def post_analytics(self, post_id: str) -> Any:
            self.calls.append(post_id)
            raise PostizError("rate limited", code="POSTIZ_HTTP_429", retryable=True)

    fake = OneBad({})
    with pytest.raises(RetryableTaskError), session_scope(world["factory"]) as db:
        handle_sync_post_metrics(db, JobRun(), clients=_factory(fake), sleep=_no_sleep)
    assert len(fake.calls) == 1  # capped at one post per run
    ok = FakePostiz(_payloads(world, 10))
    with session_scope(world["factory"]) as db:
        out = handle_sync_post_metrics(db, JobRun(), clients=_factory(ok), sleep=_no_sleep)
    assert out["checked"] == 1 and out["deferred"] >= 1
    _ = tag


def test_postiz_down_for_every_post_retries(world: dict[str, Any]) -> None:
    class DownPostiz(FakePostiz):
        def post_analytics(self, post_id: str) -> Any:
            raise PostizError("down", code="POSTIZ_NETWORK", retryable=True)

    with pytest.raises(RetryableTaskError), session_scope(world["factory"]) as db:
        handle_sync_post_metrics(db, JobRun(), clients=_factory(DownPostiz({})), sleep=_no_sleep)


def _observe(db: Any, world: dict[str, Any], slug: str, key: str, at: datetime, v: int) -> None:
    w = world[slug]
    db.add(
        PostMetricObservation(
            brand_profile_id=w["brand_id"],
            channel_account_id=w["ig_chan"],
            content_package_id=w["pkg"],
            publish_schedule_id=w["ig"],
            platform="INSTAGRAM",
            metric_key=key,
            value=Decimal(v),
            observed_at=at,
            source="test",
        )
    )


def test_reports_use_deltas_isolate_brands_and_exclude_unpublished(world: dict[str, Any]) -> None:
    now = world["now"]
    with session_scope(world["factory"]) as db:
        # 40 days ago 1,000 → 3 days ago 1,150 → 1 day ago 1,300 (cumulative lifetime).
        for days, value in ((40, 1000), (3, 1150), (1, 1300)):
            _observe(db, world, "brand-app-promo", "views", now - timedelta(days=days), value)
        _observe(db, world, "brand-tech-ai", "views", now - timedelta(days=2), 99_999)

    with session_scope(world["factory"]) as db:
        promo = world["brand-app-promo"]["brand_id"]
        all_time = service.views_report(db, promo, range_key="all", use_cache=False)
        assert all_time["totals"]["views"] == pytest.approx(1300)  # lifetime, never 3,450
        week = service.views_report(db, promo, range_key="7d", use_cache=False)
        # 1,150→1,300 lies fully inside (150); 1,000→1,150 spans 37 days of which only the last
        # few fall inside, so it contributes a prorated sliver (~16).
        assert 150 < week["totals"]["views"] < 180
        assert week["totals"]["approximate"] is True
        assert week["totals"]["published_posts"] == 2  # IG + FB; scheduled/failed excluded
        # Buckets are rounded to 2 dp individually.
        trend_sum = sum(t["views"] for t in week["trend"])
        assert trend_sum == pytest.approx(week["totals"]["views"], abs=0.1)
        assert {p["platform"] for p in week["by_platform"]} == {"INSTAGRAM", "FACEBOOK"}
        assert week["top_post"]["content_package_id"] == str(world["brand-app-promo"]["pkg"])
        assert week["breakdowns"]["hook_type"][0]["value"] == "PAIN_POINT"

        tech = world["brand-tech-ai"]["brand_id"]
        tech_report = service.views_report(db, tech, range_key="all", use_cache=False)
        assert tech_report["totals"]["views"] == pytest.approx(99_999)
        assert all_time["totals"]["views"] < 99_999  # no cross-brand leakage

        listing = service.posts_report(db, promo, range_key="7d")
        ids = {r["schedule_id"] for r in listing["items"]}
        assert ids == {str(world["brand-app-promo"]["ig"]), str(world["brand-app-promo"]["fb"])}

        detail = service.post_detail(db, promo, world["brand-app-promo"]["pkg"], range_key="30d")
        assert detail is not None
        assert detail["aggregate"]["lifetime_views"] == pytest.approx(1300)
        # Another brand cannot read this post.
        assert service.post_detail(db, tech, world["brand-app-promo"]["pkg"]) is None

        pipe = service.pipeline_summary(db, promo)
        assert pipe["publishing"]["published"] >= 2
        assert pipe["publishing"]["scheduled"] >= 1
