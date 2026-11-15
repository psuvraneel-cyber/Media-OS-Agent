"""Integration (disposable DB): schedule request → publish job → Postiz → sync → PUBLISHED.

GitHub and Postiz are replaced by in-memory fakes; slides are real PNG files on a temp media root.
"""

from __future__ import annotations

import io
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select

from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.qa import run_text_qa
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft
from media_os_api.carousel.service import create_carousel_package
from media_os_api.content.taxonomy import HookType, Language
from media_os_api.db import (
    Asset,
    AuditEvent,
    BrandProfile,
    ChannelAccount,
    ContentPackage,
    ContentStateTransition,
    JobRun,
    OutboxEvent,
    PublishSchedule,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.errors import PolicyRefusalError, RetryableTaskError
from media_os_api.publish import service as svc
from media_os_api.publish.media_host import HostedFile
from media_os_api.publish.postiz import Integration

pytestmark = pytest.mark.integration
PILLARS = ["wedding_season", "how_it_works", "wording_help", "design_showcase", "other_occasions"]
PLAY = NIMONTRON.play_url


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


class FakeHost:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def publish(self, files: list[tuple[str, bytes]], message: str) -> list[HostedFile]:
        self.published.extend(files)
        return [HostedFile(p, f"https://o.github.io/m/{p}") for p, _ in files]

    def wait_until_live(self, files: list[HostedFile], **_: Any) -> None:
        return None

    def close(self) -> None:
        return None


class FakePostiz:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        self.integrations_list = [
            Integration("ig-int", "instagram", "Nimontron: Bengali Invitations", False),
            Integration("fb-int", "facebook", "Nimontron: Bengali Invitations", False),
        ]

    def integrations(self) -> list[Integration]:
        return self.integrations_list

    def schedule_carousel(
        self,
        *,
        when: datetime,
        caption: str,
        image_urls: list[str],
        targets: list[Integration],
        image_prefix: str,
    ) -> dict[str, str]:
        out = {}
        for t in targets:
            pid = f"post-{t.id}-{len(self.created)}"
            self.created.append(
                {
                    "id": pid,
                    "integration": t.id,
                    "caption": caption,
                    "images": image_urls,
                    "when": when,
                }
            )
            self.posts.append(
                {
                    "id": pid,
                    "integration": {"id": t.id},
                    "state": "QUEUE",
                    "publishDate": when.isoformat(),
                    "creationMethod": "API",
                }
            )
            out[t.id] = pid
        return out

    def posts_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return list(self.posts)

    def close(self) -> None:
        return None


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (108, 135), (163, 22, 31)).save(buf, format="PNG")
    return buf.getvalue()


def _draft() -> CarouselDraft:
    def s(role: str, bn: str, en: str, claims: list[str]) -> Any:
        return {"role": role, "headline_bn": bn, "headline_en": en, "claim_ids": claims}

    return CarouselDraft.model_validate(
        {
            "slides": [
                s("HOOK", "বিয়ের মরশুম", "Wedding season", []),
                s("POINT", "তিন ধাপে কার্ড", "Three steps", ["speed"]),
                s("POINT", "ইংরেজিতে নাম", "Names in English", ["names_bn"]),
                s("POINT", "এক ট্যাপে পাঠান", "One tap", ["sharing"]),
                s("CTA", "গুগল প্লে থেকে নিন", "Get it on Google Play", ["on_play"]),
            ],
            "caption_bn": f"বিয়ের কার্ড। {PLAY}",
            "caption_en": f"Bengali wedding cards. {PLAY}",
            "hashtags": ["#Nimontron", "#BengaliWedding", "#বিয়েরকার্ড"],
            "alt_text_en": "Five slides about making a Bengali wedding card.",
        }
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    engine = create_db_engine(_db_url(), pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    monkeypatch.setenv("MEDIA_OS_MEDIA_ROOT", str(tmp_path))
    with session_scope(factory) as db:
        brand = db.execute(
            select(BrandProfile).where(BrandProfile.slug == "brand-app-promo")
        ).scalar_one()
        saved = (list(brand.content_pillars), dict(brand.publishing_preferences or {}))
        brand.content_pillars = PILLARS
        brand.publishing_preferences = {**saved[1], "daily_caps": {"INSTAGRAM": 1, "FACEBOOK": 1}}
        brief = CarouselBrief(
            pillar="wedding_season", hook_type=HookType.SEASONAL, language=Language.BN_EN
        )
        draft = _draft()
        pkg = create_carousel_package(
            db,
            brand_slug="brand-app-promo",
            brief=brief,
            draft=draft,
            qa=run_text_qa(NIMONTRON, brief, draft),
            score={"total": 80},
            written_by="test/model",
        )
        rel = Path("carousels") / str(pkg.id) / "v1"
        (tmp_path / rel).mkdir(parents=True)
        for i in range(1, 6):
            (tmp_path / rel / f"slide-{i:02d}.png").write_bytes(_png())
            db.add(
                Asset(
                    brand_profile_id=brand.id,
                    content_package_id=pkg.id,
                    asset_type="CAROUSEL_SLIDE",
                    local_path=f"{rel.as_posix()}/slide-{i:02d}.png",
                    mime_type="image/png",
                    file_size=10,
                    checksum="x",
                    width=108,
                    height=135,
                    status="ACTIVE",
                )
            )
        pkg.status = "QA_PASSED"
        pkg.approval_status = "APPROVED"
        pid, bid = pkg.id, brand.id
    fakes = {"host": FakeHost(), "postiz": FakePostiz()}
    try:
        yield {
            "factory": factory,
            "pid": pid,
            "bid": bid,
            **fakes,
            "clients": lambda: (fakes["host"], fakes["postiz"]),
        }
    finally:
        with session_scope(factory) as db:
            ids = list(
                db.execute(
                    select(ContentPackage.id).where(ContentPackage.format == "CAROUSEL")
                ).scalars()
            )
            db.execute(delete(PublishSchedule))
            db.execute(
                delete(ChannelAccount).where(ChannelAccount.postiz_integration_id.is_not(None))
            )
            if ids:
                db.execute(delete(Asset).where(Asset.content_package_id.in_(ids)))
                db.execute(
                    delete(ContentStateTransition).where(
                        ContentStateTransition.content_package_id.in_(ids)
                    )
                )
                db.execute(delete(ContentPackage).where(ContentPackage.id.in_(ids)))
            jobs = select(JobRun.id).where(JobRun.task_name.like("media_os.%carousel%"))
            db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(jobs)))
            db.execute(delete(JobRun).where(JobRun.task_name.like("media_os.%carousel%")))
            db.execute(delete(AuditEvent).where(AuditEvent.action.like("CAROUSEL_%")))
            restored = db.get(BrandProfile, bid)
            assert restored is not None
            restored.content_pillars, restored.publishing_preferences = saved
        engine.dispose()


def _when() -> datetime:
    return (datetime.now(UTC) + timedelta(days=2)).replace(second=0, microsecond=0)


def test_request_validates_then_job_schedules_both_platforms(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        brand = db.get(BrandProfile, env["bid"])
        assert pkg and brand
        with pytest.raises(svc.ScheduleRequestError) as soon:
            svc.request_schedule(
                db,
                pkg=pkg,
                brand=brand,
                when=datetime.now(UTC) + timedelta(minutes=5),
                platforms=["INSTAGRAM"],
                actor_id="owner:t",
            )
        assert soon.value.code == "TOO_SOON"
        job = svc.request_schedule(
            db,
            pkg=pkg,
            brand=brand,
            when=when,
            platforms=["INSTAGRAM", "FACEBOOK"],
            actor_id="owner:t",
        )
        assert job.queue_name == "research" and job.payload["platforms"] == [
            "INSTAGRAM",
            "FACEBOOK",
        ]
        result = svc.handle_schedule_carousel(db, job, clients=env["clients"])
    assert result["platforms"] == ["FACEBOOK", "INSTAGRAM"] and result["status"] == "SCHEDULED"
    host, postiz = env["host"], env["postiz"]
    assert len(host.published) == 5 and all(
        p.endswith(".jpg") and d[:3] == b"\xff\xd8\xff" for p, d in host.published
    )
    assert {c["integration"] for c in postiz.created} == {"ig-int", "fb-int"}
    assert "#Nimontron" in postiz.created[0]["caption"] and PLAY in postiz.created[0]["caption"]
    with session_scope(f) as db:
        rows = list(
            db.execute(select(PublishSchedule).order_by(PublishSchedule.platform)).scalars()
        )
        assert [(r.platform, r.status) for r in rows] == [
            ("FACEBOOK", "SCHEDULED"),
            ("INSTAGRAM", "SCHEDULED"),
        ]
        assert all(r.postiz_post_id for r in rows)
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "SCHEDULED"
        # Re-running the job does nothing new (idempotent).
        job2 = (
            db.execute(select(JobRun).where(JobRun.task_name == svc.SCHEDULE_TASK))
            .scalars()
            .first()
        )
        assert job2 and svc.handle_schedule_carousel(db, job2, clients=env["clients"])["skipped"]
    assert len(postiz.created) == 2


def test_daily_cap_refuses_second_post_same_day(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        brand = db.get(BrandProfile, env["bid"])
        assert pkg and brand
        job = svc.request_schedule(
            db, pkg=pkg, brand=brand, when=when, platforms=["INSTAGRAM"], actor_id="owner:t"
        )
        svc.handle_schedule_carousel(db, job, clients=env["clients"])
        other = ContentPackage(
            brand_profile_id=brand.id,
            platform="INSTAGRAM",
            format="CAROUSEL",
            status="QA_PASSED",
            approval_status="APPROVED",
            hashtags=[],
            slide_data=[],
            source_manifest=[],
        )
        db.add(other)
        db.flush()
        with pytest.raises(svc.ScheduleRequestError) as cap:
            svc.request_schedule(
                db,
                pkg=other,
                brand=brand,
                when=when + timedelta(hours=1),
                platforms=["INSTAGRAM"],
                actor_id="owner:t",
            )
        assert cap.value.code == "DAILY_CAP"


def test_unapproved_is_refused_by_the_job(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        brand = db.get(BrandProfile, env["bid"])
        assert pkg and brand
        job = svc.request_schedule(
            db, pkg=pkg, brand=brand, when=when, platforms=["INSTAGRAM"], actor_id="owner:t"
        )
        pkg.approval_status = "PENDING"  # e.g. revoked after the request
        db.flush()
        with pytest.raises(PolicyRefusalError):
            svc.handle_schedule_carousel(db, job, clients=env["clients"])
    assert env["postiz"].created == [] and env["host"].published == []


def test_sync_marks_published_and_package_published(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        brand = db.get(BrandProfile, env["bid"])
        assert pkg and brand
        job = svc.request_schedule(
            db,
            pkg=pkg,
            brand=brand,
            when=when,
            platforms=["INSTAGRAM", "FACEBOOK"],
            actor_id="owner:t",
        )
        svc.handle_schedule_carousel(db, job, clients=env["clients"])
        for r in db.execute(select(PublishSchedule)).scalars():  # pretend the time has passed
            r.scheduled_at = datetime.now(UTC) - timedelta(minutes=5)
    for p in env["postiz"].posts:
        p["state"] = "PUBLISHED"
        p["releaseURL"] = f"https://www.instagram.com/p/{p['id']}/"
    with session_scope(f) as db:
        out = svc.handle_sync_status(
            db, JobRun(task_name=svc.SYNC_TASK, payload={}), clients=env["clients"]
        )
        assert out == {"checked": 2, "updated": 2}
        rows = list(db.execute(select(PublishSchedule)).scalars())
        assert {r.status for r in rows} == {"PUBLISHED"} and all(
            (r.remote_post_id or "").startswith("https://") for r in rows
        )
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "PUBLISHED"
        actions = set(
            db.execute(
                select(AuditEvent.action).where(AuditEvent.action.like("CAROUSEL_%"))
            ).scalars()
        )
        assert {
            "CAROUSEL_SCHEDULE_REQUESTED",
            "CAROUSEL_SCHEDULED",
            "CAROUSEL_PUBLISHED",
        } <= actions
    _ = uuid  # keep import for readability of ids above


# ------------------------------------------------------------------ review fixes (bugs 1-4)


def _schedule(db: Any, env: dict[str, Any], when: datetime, platforms: list[str]) -> JobRun:
    pkg = db.get(ContentPackage, env["pid"])
    brand = db.get(BrandProfile, env["bid"])
    assert pkg and brand
    return svc.request_schedule(
        db, pkg=pkg, brand=brand, when=when, platforms=platforms, actor_id="owner:t"
    )


def _expire_and_sync(env: dict[str, Any]) -> None:
    with session_scope(env["factory"]) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            if r.status == "SCHEDULED":
                r.scheduled_at = datetime.now(UTC) - timedelta(hours=1)
    with session_scope(env["factory"]) as db:
        svc.handle_sync_status(
            db, JobRun(task_name=svc.SYNC_TASK, payload={}), clients=env["clients"]
        )


def test_cancelled_carousel_can_be_rescheduled_repeatedly(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    env["postiz"].posts.clear()  # owner deletes the post in Postiz
    _expire_and_sync(env)
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "FAILED_RETRYABLE" and pkg.previous_status == "SCHEDULED"
        assert svc.can_schedule(pkg, svc.package_schedules(db, pkg))
        rows = svc.package_schedules(db, pkg)
        with pytest.raises(svc.ScheduleRequestError) as same:
            _schedule(db, env, rows[0].scheduled_at, ["INSTAGRAM"])
        assert same.value.code == "SAME_TIME"
        later = when + timedelta(days=1)
        job = _schedule(db, env, later, ["INSTAGRAM"])
        out = svc.handle_schedule_carousel(db, job, clients=env["clients"])
        assert out["status"] == "SCHEDULED"
        rows = svc.package_schedules(db, pkg)
        assert sorted(r.status for r in rows) == ["CANCELLED", "SCHEDULED"]
        assert not svc.can_schedule(pkg, rows)
    # The second attempt fails too: the package must return to FAILED_RETRYABLE (fresh key).
    env["postiz"].posts.clear()
    _expire_and_sync(env)
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "FAILED_RETRYABLE"
        assert svc.can_schedule(pkg, svc.package_schedules(db, pkg))


def test_second_carousel_near_same_minute_is_refused(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
        brand = db.get(BrandProfile, env["bid"])
        assert brand
        brand.publishing_preferences = {
            **(brand.publishing_preferences or {}),
            "daily_caps": {"INSTAGRAM": 2, "FACEBOOK": 2},
        }
        other = ContentPackage(
            brand_profile_id=brand.id,
            platform="INSTAGRAM",
            format="CAROUSEL",
            status="QA_PASSED",
            approval_status="APPROVED",
            hashtags=[],
            slide_data=[],
            source_manifest=[],
        )
        db.add(other)
        db.flush()
        with pytest.raises(svc.ScheduleRequestError) as slot:
            svc.request_schedule(
                db,
                pkg=other,
                brand=brand,
                when=when + timedelta(minutes=2),
                platforms=["INSTAGRAM"],
                actor_id="owner:t",
            )
        assert slot.value.code == "SLOT_TAKEN"
        # Even if a post at that minute exists in Postiz, an id linked to another carousel is
        # never adopted by retry recovery.
        claimed = svc.claimed_post_ids(db)
        assert claimed
        assert svc.existing_posts(env["postiz"], when, {"ig-int"}, claimed) == {}


def test_missing_postiz_ids_are_recovered_or_retried(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    real = env["postiz"].schedule_carousel

    def no_ids(**kwargs: Any) -> dict[str, str]:
        real(**kwargs)  # Postiz creates the posts but reports no ids
        return {}

    env["postiz"].schedule_carousel = no_ids
    with session_scope(f) as db:
        out = svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
        assert out["status"] == "SCHEDULED"
        rows = list(db.execute(select(PublishSchedule)).scalars())
        assert len(rows) == 1 and rows[0].postiz_post_id  # recovered by lookup


def test_missing_postiz_ids_never_saved_as_none(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    env["postiz"].schedule_carousel = lambda **_: {}  # nothing created, nothing reported
    with pytest.raises(RetryableTaskError), session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    with session_scope(f) as db:
        assert list(db.execute(select(PublishSchedule)).scalars()) == []


def test_sync_never_cancels_a_schedule_without_postiz_id(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
        for r in db.execute(select(PublishSchedule)).scalars():
            r.postiz_post_id = None  # legacy row
    env["postiz"].posts.clear()
    _expire_and_sync(env)
    with session_scope(f) as db:
        assert {r.status for r in db.execute(select(PublishSchedule)).scalars()} == {"SCHEDULED"}


def test_overlong_caption_is_refused_not_truncated(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg
        pkg.caption = ("বিয়ের কার্ড " * 250) + PLAY  # > 2,200 characters, link at the end
        db.flush()
        with pytest.raises(svc.ScheduleRequestError) as long:
            _schedule(db, env, when, ["INSTAGRAM"])
        assert long.value.code == "CAPTION_TOO_LONG"
    assert env["postiz"].created == []


# ------------------------------------------------------------------ overdue / manual retry


def _sync(env: dict[str, Any]) -> dict[str, Any]:
    with session_scope(env["factory"]) as db:
        return svc.handle_sync_status(
            db, JobRun(task_name=svc.SYNC_TASK, payload={}), clients=env["clients"]
        )


def _backdate(env: dict[str, Any], minutes: int) -> None:
    with session_scope(env["factory"]) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            r.scheduled_at = datetime.now(UTC) - timedelta(minutes=minutes)


def test_queued_post_becomes_overdue_frees_cap_then_publishes_late(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    _backdate(env, 10)
    _sync(env)  # 10 min late: still just SCHEDULED
    with session_scope(f) as db:
        assert {r.status for r in db.execute(select(PublishSchedule)).scalars()} == {"SCHEDULED"}
    _backdate(env, 45)  # PC was off: Postiz still has it queued
    _sync(env)
    with session_scope(f) as db:
        row = db.execute(select(PublishSchedule)).scalar_one()
        assert row.status == "OVERDUE"
        assert (
            svc.scheduled_count(db, env["bid"], "INSTAGRAM", row.scheduled_at, "Asia/Kolkata") == 0
        )
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "SCHEDULED"  # not settled while overdue
    for p in env["postiz"].posts:  # Postiz catches up once the PC is back
        p["state"] = "PUBLISHED"
    _sync(env)
    with session_scope(f) as db:
        assert db.execute(select(PublishSchedule)).scalar_one().status == "PUBLISHED"
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "PUBLISHED"


def test_failed_post_retried_in_postiz_is_picked_up(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    _backdate(env, 60)
    for p in env["postiz"].posts:
        p["state"] = "ERROR"
    _sync(env)
    with session_scope(f) as db:
        assert db.execute(select(PublishSchedule)).scalar_one().status == "FAILED"
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "FAILED_RETRYABLE"
    for p in env["postiz"].posts:  # owner clicks "retry" in Postiz and it publishes
        p["state"] = "PUBLISHED"
        p["releaseURL"] = "https://www.instagram.com/p/retried/"
    _sync(env)
    with session_scope(f) as db:
        row = db.execute(select(PublishSchedule)).scalar_one()
        assert (
            row.status == "PUBLISHED"
            and row.remote_post_id == "https://www.instagram.com/p/retried/"
        )
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg and pkg.status == "PUBLISHED"
