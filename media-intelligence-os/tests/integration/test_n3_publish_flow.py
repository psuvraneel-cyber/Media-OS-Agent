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
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from media_os_api.auth import create_redis_client, hash_password
from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.qa import run_text_qa
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft
from media_os_api.carousel.service import create_carousel_package
from media_os_api.config import load_settings
from media_os_api.content.taxonomy import HookType, Language
from media_os_api.db import (
    Asset,
    AuditEvent,
    AuthSession,
    BrandProfile,
    ChannelAccount,
    ContentPackage,
    ContentStateTransition,
    JobRun,
    OutboxEvent,
    OwnerUser,
    PublishSchedule,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.errors import PolicyRefusalError, RetryableTaskError
from media_os_api.main import create_app
from media_os_api.notify import service as notify
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
            for pattern in ("media_os.%carousel%", "media_os.notify.%"):
                jobs = select(JobRun.id).where(JobRun.task_name.like(pattern))
                db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(jobs)))
                db.execute(delete(JobRun).where(JobRun.task_name.like(pattern)))
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
    # Midday in the brand's timezone, so "+1 hour" never crosses into the next local day.
    f = env["factory"]
    when = _when().astimezone(ZoneInfo("Asia/Kolkata")).replace(hour=12, minute=0)
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
        assert out == {"checked": 2, "updated": 2, "auto_retried": 0}
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


# ------------------------------------------------------------------ reschedule via the API

OWNER = "n3-owner"
PASSWORD = "N3-Strong-Test-Password!2026"
SECRET = "n3-test-session-secret-0123456789abcdef0123456789abcdef0123"
ORIGIN = "http://localhost:3000"


@pytest.fixture
def client(env: dict[str, Any]) -> Iterator[TestClient]:
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    if not redis_url:
        pytest.skip("MEDIA_OS_TEST_REDIS_URL not set.")
    rc = create_redis_client(redis_url)
    for key in rc.scan_iter("media_os:rl:login:*"):
        rc.delete(key)
    rc.close()
    with session_scope(env["factory"]) as db:
        db.execute(delete(AuthSession))
        db.execute(delete(OwnerUser))
    settings = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": OWNER,
            "OWNER_PASSWORD_HASH": hash_password(PASSWORD),
            "SESSION_SECRET": SECRET,
            "COOKIE_SECURE": "false",
            "CORS_ALLOWED_ORIGINS": ORIGIN,
            "DATABASE_URL": _db_url(),
            "REDIS_URL": redis_url,
        }
    )
    with TestClient(create_app(settings)) as c:
        r = c.post(
            "/api/v1/auth/login",
            json={"username": OWNER, "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert r.status_code == 200
        c.headers.update({"X-CSRF-Token": str(r.json()["csrf_token"]), "Origin": ORIGIN})
        yield c


def _api_schedule(c: TestClient, pid: uuid.UUID, when: datetime) -> Any:
    return c.post(
        f"/api/v1/content/carousels/{pid}/schedule?brand=brand-app-promo",
        json={"scheduled_at": when.isoformat(), "platforms": ["INSTAGRAM", "FACEBOOK"]},
    )


def _run_job(env: dict[str, Any], when: datetime) -> dict[str, Any]:
    with session_scope(env["factory"]) as db:
        job = db.execute(
            select(JobRun).where(
                JobRun.task_name == svc.SCHEDULE_TASK,
                JobRun.payload["scheduled_at"].astext == when.astimezone(UTC).isoformat(),
            )
        ).scalar_one()
        out = svc.handle_schedule_carousel(db, job, clients=env["clients"])
        job.status = "SUCCEEDED"  # what the job runner records after the handler returns
        return out


def test_failed_carousel_is_rescheduled_through_the_api(
    env: dict[str, Any], client: TestClient
) -> None:
    f, pid, when = env["factory"], env["pid"], _when()
    detail_url = f"/api/v1/content/carousels/{pid}?brand=brand-app-promo"
    assert _api_schedule(client, pid, when).status_code == 200
    assert _run_job(env, when)["status"] == "SCHEDULED"
    # A live schedule blocks a second schedule, exactly as before.
    blocked = _api_schedule(client, pid, when + timedelta(days=1))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ALREADY_SCHEDULED"

    # Meta refuses both posts (e.g. OAuthException 200): Postiz marks them ERROR.
    _backdate(env, 60)
    for p in env["postiz"].posts:
        p["state"] = "ERROR"
    _sync(env)
    detail = client.get(detail_url).json()
    assert detail["status"] == "FAILED_RETRYABLE" and detail["can_schedule"] is True
    assert sorted(s["status"] for s in detail["schedules"]) == ["FAILED", "FAILED"]
    with session_scope(f) as db:
        old_keys = {r.idempotency_key for r in db.execute(select(PublishSchedule)).scalars()}
        old_posts = {r.postiz_post_id for r in db.execute(select(PublishSchedule)).scalars()}

    later = when + timedelta(days=1)
    res = _api_schedule(client, pid, later)
    assert res.status_code == 200, res.text
    assert res.json()["scheduling"] is True
    # The in-flight job blocks a double click before the worker has run.
    assert _api_schedule(client, pid, later + timedelta(hours=1)).status_code == 409
    out = _run_job(env, later)
    assert out["status"] == "SCHEDULED" and out["platforms"] == ["FACEBOOK", "INSTAGRAM"]
    assert len(env["postiz"].created) == 4  # two new Postiz posts

    with session_scope(f) as db:
        rows = list(db.execute(select(PublishSchedule)).scalars())
        assert sorted((r.platform, r.status) for r in rows) == [
            ("FACEBOOK", "FAILED"),
            ("FACEBOOK", "SCHEDULED"),
            ("INSTAGRAM", "FAILED"),
            ("INSTAGRAM", "SCHEDULED"),
        ]
        new = [r for r in rows if r.status == "SCHEDULED"]
        assert {r.idempotency_key for r in new}.isdisjoint(old_keys)
        assert {r.postiz_post_id for r in new}.isdisjoint(old_posts)
        assert all(r.scheduled_at == later for r in new)
        moves = [
            (t.from_status, t.to_status)
            for t in db.execute(
                select(ContentStateTransition)
                .where(ContentStateTransition.content_package_id == pid)
                .order_by(ContentStateTransition.created_at)
            ).scalars()
        ]
        assert moves[-2:] == [("SCHEDULED", "FAILED_RETRYABLE"), ("FAILED_RETRYABLE", "SCHEDULED")]
        requested = list(
            db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "CAROUSEL_SCHEDULE_REQUESTED")
                .order_by(AuditEvent.timestamp)
            ).scalars()
        )
        assert [a.event_metadata.get("reschedule") for a in requested] == [False, True]
        scheduled = db.execute(
            select(AuditEvent).where(AuditEvent.action == "CAROUSEL_SCHEDULED")
        ).all()
        assert len(scheduled) == 2

    # Now live again: further schedule attempts are refused while it is SCHEDULED.
    again = _api_schedule(client, pid, later + timedelta(days=1))
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_SCHEDULED"
    detail = client.get(detail_url).json()
    assert detail["status"] == "SCHEDULED" and detail["can_schedule"] is False


# ------------------------------------------------------------------ owner alerts

META_ERROR = (
    '{"details":[{"json":"{\\"error\\":{\\"message\\":\\"Cannot call API for app 1 on behalf '
    'of user 2\\",\\"type\\":\\"OAuthException\\",\\"code\\":200}}"}]}'
)


def _alerts(env: dict[str, Any], kind: str = "") -> list[JobRun]:
    """Queued alert jobs, optionally only one kind (the alert key prefix, e.g. "stuck")."""
    with session_scope(env["factory"]) as db:
        rows = list(
            db.execute(
                select(JobRun)
                .where(
                    JobRun.task_name == notify.NOTIFY_TASK,
                    JobRun.idempotency_key.like(f"notify:{kind}%"),
                )
                .order_by(JobRun.created_at)
            ).scalars()
        )
        db.expunge_all()
        return rows


def test_failed_post_queues_a_plain_language_alert(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    _backdate(env, 60)
    for p in env["postiz"].posts:
        p["state"] = "ERROR"
        p["error"] = META_ERROR
    _sync(env)
    _sync(env)  # a second sync must not alert again
    alerts = _alerts(env)
    assert len(alerts) == 1
    text = alerts[0].payload["text"]
    assert "Post failed on Instagram" in text and "Meta blocked the app" in text
    assert alerts[0].payload["severity"] == "error"


def test_published_post_queues_a_quiet_alert_with_the_link(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    _backdate(env, 20)
    for p in env["postiz"].posts:
        p["state"] = "PUBLISHED"
        p["releaseURL"] = "https://www.instagram.com/p/live/"
    _sync(env)
    (alert,) = _alerts(env)
    assert "Posted on Instagram" in alert.payload["text"]
    assert "https://www.instagram.com/p/live/" in alert.payload["text"]
    assert alert.payload["silent"] is True


def test_watchdog_flags_a_stuck_post_once(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )

    def watch() -> dict[str, Any]:
        with session_scope(f) as db:
            return notify.handle_watchdog(
                db, JobRun(task_name=notify.WATCHDOG_TASK, payload={}), postiz=lambda: env["postiz"]
            )

    _backdate(env, 5)
    assert watch()["stuck"] == 0  # not late enough yet
    assert _alerts(env, "stuck") == []
    _backdate(env, 15)  # Postiz still has it queued: its worker isn't publishing
    assert watch()["stuck"] == 1
    _backdate(env, 45)
    watch()
    _sync(env)  # the sync marks it OVERDUE: same alert key, so no second message
    alerts = _alerts(env, "stuck")
    assert len(alerts) == 1 and "hasn't gone out" in alerts[0].payload["text"]


class _DownPostiz:
    def integrations(self) -> list[Integration]:
        from media_os_api.publish.postiz import PostizError

        raise PostizError("connection refused", code="POSTIZ_NETWORK", retryable=True)

    def close(self) -> None:
        return None


def test_watchdog_reports_postiz_down_once_per_hour(env: dict[str, Any]) -> None:
    for _ in range(2):
        with session_scope(env["factory"]) as db:
            out = notify.handle_watchdog(
                db, JobRun(task_name=notify.WATCHDOG_TASK, payload={}), postiz=_DownPostiz
            )
        assert out["postiz"] == "POSTIZ_NETWORK"
    (alert,) = _alerts(env, "postiz-down")
    assert "Postiz isn't responding" in alert.payload["text"]


def test_job_that_gives_up_alerts_the_owner(env: dict[str, Any]) -> None:
    from media_os_api.jobs.errors import PermanentTaskError
    from media_os_api.jobs.ledger import complete_job_failure

    with session_scope(env["factory"]) as db:
        job = _schedule(db, env, _when(), ["INSTAGRAM"])
        job.status = "RUNNING"
        job.attempt_count = 1
        db.flush()
        complete_job_failure(
            db, job_run_id=job.id, exc=PermanentTaskError("bad slides", code="SLIDE_COUNT")
        )
        assert job.status == "FAILED_FINAL"
    (alert,) = _alerts(env)
    assert "failed for good" in alert.payload["text"] and "SLIDE_COUNT" in alert.payload["text"]


def test_daily_digest_summarises_the_day(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    with session_scope(f) as db:
        out = notify.handle_daily_digest(
            db,
            JobRun(id=uuid.uuid4(), task_name=notify.DIGEST_TASK, payload={}),
            now=when - timedelta(hours=2),
        )
    assert out["upcoming"] == 1
    (alert,) = _alerts(env, "digest")
    assert "Coming up (next 24 h):</b> 1" in alert.payload["text"]


def test_watchdog_reports_when_status_checks_stop(env: dict[str, Any]) -> None:
    # The sync schedule is enabled (migration 0009) but no sync has succeeded recently.
    with session_scope(env["factory"]) as db:
        out = notify.handle_watchdog(
            db, JobRun(task_name=notify.WATCHDOG_TASK, payload={}), postiz=lambda: env["postiz"]
        )
    assert out["sync"].startswith("stale")
    (alert,) = _alerts(env, "sync-stale")
    assert "status checks have stopped" in alert.payload["text"]


def test_nightly_report_adds_up_the_last_7_days(env: dict[str, Any]) -> None:
    from decimal import Decimal

    from media_os_api.db import PostMetricObservation

    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM", "FACEBOOK"]), clients=env["clients"]
        )
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    published_at = now - timedelta(days=3)
    # Lifetime counters as the platforms report them: two readings per post.
    readings = {
        "INSTAGRAM": {"views": (100, 300), "likes": (10, 25), "comments": (1, 3)},
        "FACEBOOK": {"reactions": (4, 9), "unique_viewers": (50, 80)},
    }
    try:
        with session_scope(f) as db:
            for row in db.execute(select(PublishSchedule)).scalars():
                row.status, row.scheduled_at = "PUBLISHED", published_at
                for key, (first, last) in readings[row.platform].items():
                    for at, value in (
                        (now - timedelta(days=2), first),
                        (now - timedelta(hours=1), last),
                    ):
                        db.add(
                            PostMetricObservation(
                                brand_profile_id=row.brand_profile_id,
                                channel_account_id=row.channel_account_id,
                                content_package_id=row.content_package_id,
                                publish_schedule_id=row.id,
                                platform=row.platform,
                                metric_key=key,
                                value_kind="CUMULATIVE",
                                value=Decimal(value),
                                observed_at=at,
                                source="test",
                            )
                        )
        with session_scope(f) as db:
            out = notify.handle_daily_digest(
                db, JobRun(id=uuid.uuid4(), task_name=notify.DIGEST_TASK, payload={}), now=now
            )
        # Views only from Instagram; likes (IG) + reactions (FB) together; reach-type metrics
        # (unique viewers) are never added across platforms.
        assert out["week"] == {"views": 300, "likes_reactions": 34, "comments": 3}
        (alert,) = _alerts(env, "digest")
        text = alert.payload["text"]
        assert "Last 7 days" in text and "Likes &amp; reactions 34" in text
        assert "<b>Facebook</b> (1 posts): Reactions 9 · Unique viewers 80" in text
        assert "Facebook comments and shares aren't available" in text
        assert "YouTube isn't connected" in text
    finally:
        with session_scope(f) as db:
            db.execute(delete(PostMetricObservation))


# ------------------------------------------------------------------ auto-retry


def _fail_all(env: dict[str, Any], error: str) -> None:
    for p in env["postiz"].posts:
        if p["state"] == "QUEUE":
            p["state"] = "ERROR"
            p["error"] = error


RATE_LIMIT = (
    '{"details":[{"json":"{\\"error\\":{\\"message\\":\\"Application request limit reached\\",'
    '\\"code\\":4}}"}]}'
)


def _schedule_jobs(env: dict[str, Any]) -> list[JobRun]:
    with session_scope(env["factory"]) as db:
        rows = list(
            db.execute(
                select(JobRun)
                .where(JobRun.task_name == svc.SCHEDULE_TASK)
                .order_by(JobRun.created_at)
            ).scalars()
        )
        db.expunge_all()
        return rows


def test_temporary_failure_is_retried_once_automatically(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    _backdate(env, 60)
    _fail_all(env, RATE_LIMIT)
    out = _sync(env)
    assert out["auto_retried"] == 1
    jobs = _schedule_jobs(env)
    assert len(jobs) == 2  # the original + the automatic retry
    retry_at = datetime.fromisoformat(jobs[-1].payload["scheduled_at"])
    assert timedelta(minutes=30) <= retry_at - datetime.now(UTC) <= timedelta(minutes=32)
    (alert,) = _alerts(env, "failed")
    assert "retry automatically" in alert.payload["text"]
    assert alert.payload["severity"] == "warning"

    # The retry runs and fails again: no third attempt, the owner is told to step in.
    with session_scope(f) as db:
        job = db.get(JobRun, jobs[-1].id)
        assert job is not None
        svc.handle_schedule_carousel(db, job, clients=env["clients"])
    with session_scope(f) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            if r.status == "SCHEDULED":
                r.scheduled_at = datetime.now(UTC) - timedelta(minutes=45)
    _fail_all(env, RATE_LIMIT)
    out = _sync(env)
    assert out["auto_retried"] == 0
    assert len(_schedule_jobs(env)) == 2
    texts = [a.payload["text"] for a in _alerts(env, "failed")]
    assert len(texts) == 2 and "reschedule it from Approvals" in texts[-1]


def test_permission_failure_is_never_retried(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    _backdate(env, 60)
    _fail_all(env, META_ERROR)
    assert _sync(env)["auto_retried"] == 0
    assert len(_schedule_jobs(env)) == 1
    (alert,) = _alerts(env, "failed")
    assert "Meta blocked the app" in alert.payload["text"]
    assert "reschedule it from Approvals" in alert.payload["text"]


# ------------------------------------------------------------------ bot commands


class _FakeBot:
    chat_id = "4242"

    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates
        self.offsets: list[int | None] = []
        self.sent: list[str] = []

    def get_updates(self, *, offset: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def send_message(self, text: str, **_: Any) -> Any:
        self.sent.append(text)
        return None

    def close(self) -> None:
        return None


def _update(update_id: int, chat: str, text: str, age_minutes: int = 0) -> dict[str, Any]:
    sent = int((datetime.now(UTC) - timedelta(minutes=age_minutes)).timestamp())
    return {
        "update_id": update_id,
        "message": {"chat": {"id": int(chat)}, "date": sent, "text": text},
    }


def test_bot_answers_the_owner_only_and_remembers_its_place(env: dict[str, Any]) -> None:
    from media_os_api.jobs.ledger import enqueue_durable_job
    from media_os_api.notify import commands

    f, when = env["factory"], _when()
    with session_scope(f) as db:
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM"]), clients=env["clients"]
        )
    bot = _FakeBot(
        [
            _update(10, "4242", "/status"),
            _update(11, "999", "/status"),  # a stranger: never answered
            _update(12, "4242", "/queue", age_minutes=30),  # too old: skipped
            _update(13, "4242", "hello"),  # not a command
            _update(14, "4242", "/queue"),
        ]
    )
    try:
        with session_scope(f) as db:
            job, _ = enqueue_durable_job(
                db,
                task_name=commands.COMMANDS_TASK,
                task_type="NOTIFY",
                payload={},
                idempotency_key="test:commands:1",
            )
            out = commands.handle_telegram_commands(db, job, telegram=lambda: bot)  # type: ignore[arg-type,return-value]
            job.status, job.result_payload = "SUCCEEDED", out
            job.worker_finished_at = datetime.now(UTC)
        assert out == {"offset": 15, "handled": 2, "ignored_other_chats": 1}
        assert "Media OS status" in bot.sent[0] and "Next post:" in bot.sent[0]
        assert "scheduled post(s)" in bot.sent[1]
        # The next run continues after the last update it saw.
        with session_scope(f) as db:
            job2, _ = enqueue_durable_job(
                db,
                task_name=commands.COMMANDS_TASK,
                task_type="NOTIFY",
                payload={},
                idempotency_key="test:commands:2",
            )
            commands.handle_telegram_commands(db, job2, telegram=lambda: bot)  # type: ignore[arg-type,return-value]
        assert bot.offsets == [None, 15] and len(bot.sent) == 2
    finally:
        with session_scope(f) as db:
            ids = select(JobRun.id).where(JobRun.task_name == commands.COMMANDS_TASK)
            db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(ids)))
            db.execute(delete(JobRun).where(JobRun.task_name == commands.COMMANDS_TASK))
