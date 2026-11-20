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
from sqlalchemy import delete, func, select

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
from media_os_api.jobs.errors import PermanentTaskError, PolicyRefusalError, RetryableTaskError
from media_os_api.main import create_app
from media_os_api.notify import service as notify
from media_os_api.publish import service as svc
from media_os_api.publish.media_host import HostedFile
from media_os_api.publish.postiz import Integration
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

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
        self.removed: list[str] = []

    def publish(self, files: list[tuple[str, bytes]], message: str) -> list[HostedFile]:
        self.published.extend(files)
        return [HostedFile(p, f"https://o.github.io/m/{p}") for p, _ in files]

    def wait_until_live(self, files: list[HostedFile], **_: Any) -> None:
        return None

    def remove_matching(self, wanted: Any, message: str) -> list[str]:
        gone = sorted({p for p, _ in self.published if wanted(p)})
        self.published = [(p, d) for p, d in self.published if p not in gone]
        self.removed.extend(gone)
        return gone

    def close(self) -> None:
        return None


class FakePostiz:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        self.deleted: list[str] = []
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

    def account_analytics(self, integration_id: str, days: int) -> list[dict[str, Any]]:
        if integration_id == "ig-int":
            return [
                {"label": "Views", "data": [{"total": 100 * days, "date": "2026-11-16"}]},
                {"label": "Likes", "data": [{"total": days, "date": "2026-11-16"}]},
                {"label": "Reach", "data": [{"total": 2}, {"total": 3}]},
            ]
        return [
            {"label": "Media views", "data": [{"total": 4}, {"total": 6}]},
            {"label": "Posts Engagement", "data": [{"total": 1}, {"total": 2}]},
        ]

    def posts_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return list(self.posts)

    def delete_post(self, post_id: str) -> None:
        # Postiz deletes the whole group (every channel); tests treat one carousel as one group.
        self.deleted.append(post_id)
        self.posts.clear()

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
    monkeypatch.setenv("MEDIA_OS_UPLOADS_ROOT", str(tmp_path / "uploads"))
    with session_scope(factory) as db:
        brand = db.execute(
            select(BrandProfile).where(BrandProfile.slug == "brand-app-promo")
        ).scalar_one()
        saved = (list(brand.content_pillars), dict(brand.publishing_preferences or {}))
        saved_identity = dict(brand.visual_identity or {})
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
                    select(ContentPackage.id).where(
                        ContentPackage.format.in_(("CAROUSEL", "REEL", "POST"))
                    )
                ).scalars()
            )
            db.execute(
                delete(Asset).where(
                    Asset.asset_type.in_(("BRAND_LOGO", "UPLOAD_IMAGE", "TEMPLATE_IMAGE"))
                )
            )
            from media_os_api.db.models import DesignTemplate

            db.execute(delete(DesignTemplate))
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
            for pattern in ("media_os.%carousel%", "media_os.notify.%", "media_os.content.%"):
                jobs = select(JobRun.id).where(JobRun.task_name.like(pattern))
                db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(jobs)))
                db.execute(delete(JobRun).where(JobRun.task_name.like(pattern)))
            manual = select(JobRun.id).where(JobRun.idempotency_key.like("analytics:manual:%"))
            db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(manual)))
            db.execute(delete(JobRun).where(JobRun.idempotency_key.like("analytics:manual:%")))
            for prefix in (
                "CAROUSEL_%",
                "REEL_%",
                "POST_%",
                "BRAND_LOGO_%",
                "ANALYTICS_SYNC_%",
                "GENERATION_%",
                "AUTOPILOT_%",
                "DESIGN_%",
            ):
                db.execute(delete(AuditEvent).where(AuditEvent.action.like(prefix)))
            restored = db.get(BrandProfile, bid)
            assert restored is not None
            restored.content_pillars, restored.publishing_preferences = saved
            restored.visual_identity = saved_identity
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


# ------------------------------------------------------------------ cancel and move


def _schedule_done(env: dict[str, Any], when: datetime, platforms: list[str]) -> None:
    """Schedule the env carousel and finish its job, as the worker would."""
    with session_scope(env["factory"]) as db:
        job = _schedule(db, env, when, platforms)
        svc.handle_schedule_carousel(db, job, clients=env["clients"])
        job.status = "SUCCEEDED"


def _run_cancel_jobs(env: dict[str, Any]) -> list[dict[str, Any]]:
    outs = []
    with session_scope(env["factory"]) as db:
        jobs = list(
            db.execute(
                select(JobRun).where(
                    JobRun.task_name == svc.CANCEL_TASK, JobRun.status == "PENDING"
                )
            ).scalars()
        )
        for job in jobs:
            outs.append(svc.handle_cancel_post(db, job, clients=env["clients"]))
            job.status = "SUCCEEDED"
    return outs


def test_owner_cancels_a_scheduled_post_from_media_os(
    env: dict[str, Any], client: TestClient
) -> None:
    f, when = env["factory"], _when()
    _schedule_done(env, when, ["INSTAGRAM", "FACEBOOK"])
    url = f"/api/v1/content/carousels/{env['pid']}"
    detail = client.get(f"{url}?{BRAND_Q}").json()
    assert detail["can_cancel"] is True

    res = client.post(f"{url}/cancel?{BRAND_Q}", json={})
    assert res.status_code == 202 and res.json()["scheduling"] is True
    again = client.post(f"{url}/cancel?{BRAND_Q}", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_CANCELLING"

    [out] = _run_cancel_jobs(env)
    assert out["cancelled"] == 2 and out["status"] == "FAILED_RETRYABLE"
    assert env["postiz"].deleted  # deleted in Postiz
    with session_scope(f) as db:
        assert {r.status for r in db.execute(select(PublishSchedule)).scalars()} == {"CANCELLED"}
    after = client.get(f"{url}?{BRAND_Q}").json()
    assert after["can_schedule"] is True and after["can_cancel"] is False
    nothing = client.post(f"{url}/cancel?{BRAND_Q}", json={})
    assert nothing.status_code == 409 and nothing.json()["error"]["code"] == "NOTHING_TO_CANCEL"


def test_owner_moves_a_post_to_a_new_time(env: dict[str, Any], client: TestClient) -> None:
    f, when = env["factory"], _when()
    _schedule_done(env, when, ["INSTAGRAM"])
    later = when + timedelta(days=1, hours=2)
    url = f"/api/v1/content/carousels/{env['pid']}/cancel?{BRAND_Q}"
    same = client.post(url, json={"move_to": when.isoformat()})
    assert same.status_code == 409 and same.json()["error"]["code"] == "SAME_TIME"
    res = client.post(url, json={"move_to": later.isoformat()})
    assert res.status_code == 202

    [out] = _run_cancel_jobs(env)
    assert out["moved_to"] == later.astimezone(UTC).isoformat()
    with session_scope(f) as db:
        job = db.execute(
            select(JobRun).where(JobRun.task_name == svc.SCHEDULE_TASK, JobRun.status == "PENDING")
        ).scalar_one()
        assert job.payload["scheduled_at"] == later.astimezone(UTC).isoformat()
        assert job.payload["platforms"] == ["INSTAGRAM"]
        svc.handle_schedule_carousel(db, job, clients=env["clients"])
    with session_scope(f) as db:
        rows = {r.status: r for r in db.execute(select(PublishSchedule)).scalars()}
        assert set(rows) == {"CANCELLED", "SCHEDULED"}
        assert rows["SCHEDULED"].scheduled_at == later
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg is not None and pkg.status == "SCHEDULED"


def test_cant_cancel_in_the_last_minutes(env: dict[str, Any]) -> None:
    f, when = env["factory"], _when()
    _schedule_done(env, when, ["INSTAGRAM"])
    with session_scope(f) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            r.scheduled_at = datetime.now(UTC) + timedelta(minutes=3)
    with session_scope(f) as db:
        pkg = db.get(ContentPackage, env["pid"])
        brand = db.get(BrandProfile, env["bid"])
        assert pkg and brand
        with pytest.raises(svc.ScheduleRequestError) as late:
            svc.request_cancel(db, pkg=pkg, brand=brand, actor_id="owner:t")
        assert late.value.code == "TOO_LATE"


# ------------------------------------------------------------------ hosted media clean-up


def test_published_media_is_removed_from_github_pages_after_three_days(
    env: dict[str, Any],
) -> None:
    from media_os_api.publish import cleanup

    f, when = env["factory"], _when()
    _schedule_done(env, when, ["INSTAGRAM", "FACEBOOK"])
    hosted = [p for p, _ in env["host"].published]
    assert hosted and all(f"/{env['pid']}/" in p for p in hosted)
    for p in env["postiz"].posts:
        p["state"] = "PUBLISHED"
    with session_scope(f) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            r.scheduled_at = datetime.now(UTC) - timedelta(days=1)
    with session_scope(f) as db:
        svc.handle_sync_status(
            db, JobRun(task_name=svc.SYNC_TASK, payload={}), clients=env["clients"]
        )

    def run() -> dict[str, Any]:
        with session_scope(f) as db:
            return cleanup.handle_cleanup_media(
                db,
                JobRun(task_name=cleanup.CLEANUP_TASK, payload={}),
                host_factory=lambda: env["host"],
            )

    assert run() == {"packages": 0, "files": 0}  # published a day ago: kept for now
    with session_scope(f) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            r.scheduled_at = datetime.now(UTC) - timedelta(days=4)
    assert run() == {"packages": 1, "files": len(hosted)}
    assert sorted(env["host"].removed) == sorted(hosted)
    assert run() == {"packages": 0, "files": 0}  # never looked at twice


def test_failed_posts_keep_their_media_for_manual_retries(env: dict[str, Any]) -> None:
    from media_os_api.publish import cleanup

    f, when = env["factory"], _when()
    _schedule_done(env, when, ["INSTAGRAM"])
    with session_scope(f) as db:
        for r in db.execute(select(PublishSchedule)).scalars():
            r.status = "FAILED"
            r.scheduled_at = datetime.now(UTC) - timedelta(days=5)
    with session_scope(f) as db:
        assert cleanup.settled_packages(db, datetime.now(UTC)) == []
        assert len(cleanup.settled_packages(db, datetime.now(UTC) + timedelta(days=11))) == 1


def test_media_paths_are_matched_to_their_post() -> None:
    from media_os_api.publish.cleanup import belongs_to

    pid = uuid.uuid4()
    assert belongs_to(f"brand-app-promo/2026-11/{pid}/v1/slide-01.jpg", "brand-app-promo", pid)
    assert not belongs_to(f"other-brand/2026-11/{pid}/v1/slide-01.jpg", "brand-app-promo", pid)
    assert not belongs_to(
        f"brand-app-promo/2026-11/{uuid.uuid4()}/v1/a.jpg", "brand-app-promo", pid
    )
    assert not belongs_to("index.html", "brand-app-promo", pid)


# ------------------------------------------------------------------ lost slides and re-render


def test_lost_slide_files_are_flagged_block_approval_and_can_be_rerendered(
    env: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from media_os_api.carousel.render_job import RENDER_TASK

    pid = _pending_carousel(env, client, monkeypatch, "test:rerender:1")
    rel = Path("carousels") / pid / "v1"
    (tmp_path / rel).mkdir(parents=True)
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, uuid.UUID(pid))
        assert pkg is not None
        for i in range(1, 4):
            # Docker stopped before the data reached the disk: the files are empty.
            (tmp_path / rel / f"slide-{i:02d}.png").write_bytes(b"")
            db.add(
                Asset(
                    brand_profile_id=pkg.brand_profile_id,
                    content_package_id=pkg.id,
                    asset_type="CAROUSEL_SLIDE",
                    local_path=f"{rel.as_posix()}/slide-{i:02d}.png",
                    mime_type="image/png",
                    file_size=900_000,
                    checksum="x",
                    width=1080,
                    height=1350,
                    status="ACTIVE",
                )
            )
        # The first render finished (its job is done) but the files were lost afterwards.
        for job in db.execute(
            select(JobRun).where(
                JobRun.task_name == RENDER_TASK, JobRun.payload["content_package_id"].astext == pid
            )
        ).scalars():
            job.status = "SUCCEEDED"
        transition_content_status(
            db, content_package_id=pkg.id, to_status=ContentLifecycleStatus.RENDERED, actor_id="t"
        )
        transition_content_status(
            db, content_package_id=pkg.id, to_status=ContentLifecycleStatus.QA_PASSED, actor_id="t"
        )

    url = f"/api/v1/content/carousels/{pid}"
    detail = client.get(f"{url}?{BRAND_Q}").json()
    assert detail["status"] == "QA_PASSED" and detail["slides_missing"] == 3
    queue = client.get(f"/api/v1/content/carousels?{BRAND_Q}").json()["items"]
    assert next(i for i in queue if i["id"] == pid)["slides_missing"] == 3

    approve = client.post(f"{url}/decision?{BRAND_Q}", json={"decision": "APPROVE"})
    assert approve.status_code == 409 and approve.json()["error"]["code"] == "SLIDES_MISSING"

    res = client.post(f"{url}/rerender?{BRAND_Q}")
    assert res.status_code == 200 and res.json()["status"] == "DRAFTED"
    again = client.post(f"{url}/rerender?{BRAND_Q}")
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_RENDERING"
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, uuid.UUID(pid))
        assert pkg is not None and pkg.version == 2
        keys = set(
            db.execute(
                select(JobRun.idempotency_key).where(
                    JobRun.task_name == RENDER_TASK,
                    JobRun.payload["content_package_id"].astext == pid,
                )
            ).scalars()
        )
        assert f"render:{pid}:v2" in keys


def test_scheduling_refuses_empty_slide_files(env: dict[str, Any], tmp_path: Path) -> None:
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg is not None
        first = (
            db.execute(
                select(Asset).where(Asset.content_package_id == pkg.id).order_by(Asset.local_path)
            )
            .scalars()
            .first()
        )
        assert first is not None
        (tmp_path / first.local_path).write_bytes(b"")
        with pytest.raises(PermanentTaskError) as err:
            svc._slides(db, pkg)
        assert err.value.code == "SLIDE_MISSING" and "empty" in str(err.value)


# ------------------------------------------------------------------ learning loop (N5)


def _published_posts(env: dict[str, Any], specs: list[tuple[str, str, int]]) -> list[uuid.UUID]:
    """Published Instagram posts with metrics: (format, hook, views at 96 h) each, a day apart."""
    from decimal import Decimal

    from media_os_api.db.models import PostMetricObservation

    now = datetime.now(UTC)
    made: list[uuid.UUID] = []
    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        svc.sync_channels(db, brand, env["postiz"])
        chan = db.execute(
            select(ChannelAccount).where(
                ChannelAccount.brand_profile_id == brand.id, ChannelAccount.platform == "INSTAGRAM"
            )
        ).scalar_one()
        for i, (fmt, hook, views) in enumerate(specs):
            pkg = ContentPackage(
                brand_profile_id=brand.id,
                platform="INSTAGRAM",
                format=fmt,
                status="PUBLISHED",
                approval_status="APPROVED",
                title=f"Learning post {i}",
                hashtags=[],
                slide_data=[],
                source_manifest=[],
                post_tags={"hook_type": hook, "language": "BN_EN", "pillar": "wedding_season"}
                if fmt == "CAROUSEL"
                else None,
            )
            db.add(pkg)
            db.flush()
            when = now - timedelta(days=10 - i, hours=1)
            sched = PublishSchedule(
                brand_profile_id=brand.id,
                channel_account_id=chan.id,
                content_package_id=pkg.id,
                platform="INSTAGRAM",
                postiz_post_id=f"learn-{i}",
                scheduled_at=when,
                status="PUBLISHED",
                idempotency_key=f"learn-{uuid.uuid4().hex}",
            )
            db.add(sched)
            db.flush()
            for hours, share in ((24, 0.5), (96, 1.0)):
                db.add(
                    PostMetricObservation(
                        brand_profile_id=brand.id,
                        channel_account_id=chan.id,
                        content_package_id=pkg.id,
                        publish_schedule_id=sched.id,
                        platform="INSTAGRAM",
                        metric_key="views",
                        value=Decimal(int(views * share)),
                        observed_at=when + timedelta(hours=hours),
                        source="test",
                    )
                )
            made.append(sched.id)
    return made


def _drop_observations(env: dict[str, Any]) -> None:
    from media_os_api.db.models import PostMetricObservation

    with session_scope(env["factory"]) as db:
        db.execute(delete(PostMetricObservation).where(PostMetricObservation.source == "test"))


def test_learning_report_measures_posts_and_holds_back_verdicts(
    env: dict[str, Any], client: TestClient
) -> None:
    from media_os_api.learning.weekly import WEEKLY_TASK, handle_weekly_report

    _published_posts(
        env,
        [
            ("CAROUSEL", "QUESTION", 10),
            ("CAROUSEL", "MISTAKE", 8),
            ("CAROUSEL", "QUESTION", 12),
            ("REEL", "", 300),
            ("CAROUSEL", "MISTAKE", 9),
        ],
    )
    try:
        res = client.get(f"/api/v1/analytics/learning?{BRAND_Q}")
        assert res.status_code == 200, res.text
        r = res.json()
        assert r["counts"] == {"published": 5, "measured": 5, "waiting_for_72h": 0}
        reel = next(p for p in r["posts"] if p["features"]["format"] == "REEL")
        # Views at 72 h are interpolated between the 24 h (150) and 96 h (300) syncs.
        assert reel["views_72h"] == 250 and reel["actual_lift"] > 10
        assert reel["predicted_lift"] is not None
        formats = {g["value"]: g for g in r["groups"]["format"]}
        assert formats["REEL"]["verdict"] == "NOT_ENOUGH_DATA" and formats["REEL"]["needed"] == 29
        assert r["model"]["active"] is False and r["winners"] == {}
        assert r["best_times"]["recommended"] == []

        with session_scope(env["factory"]) as db:
            job = JobRun(task_name=WEEKLY_TASK, payload={"trigger": "manual"})
            out = handle_weekly_report(db, job)
        assert out["sent"] == ["brand-app-promo"]
        [alert] = _alerts(env, "weekly")
        text = alert.payload["text"]
        assert "Weekly insights" in text and "No firm conclusions yet" in text
    finally:
        _drop_observations(env)


def test_weekly_report_only_goes_out_on_mondays(env: dict[str, Any]) -> None:
    from media_os_api.learning.weekly import WEEKLY_TASK, handle_weekly_report

    tuesday = datetime(2026, 11, 24, 3, 30, tzinfo=UTC)  # 09:00 IST on a Tuesday
    with session_scope(env["factory"]) as db:
        out = handle_weekly_report(
            db, JobRun(task_name=WEEKLY_TASK, payload={"trigger": "schedule"}), now=tuesday
        )
    assert out == {"sent": [], "skipped": "not Monday"}


def test_generated_drafts_carry_strategy_and_a_learned_prediction(
    env: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = _pending_carousel(env, client, monkeypatch, "test:learning:1")
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, uuid.UUID(pid))
        assert pkg is not None
        learned = (pkg.score or {})["learned"]
        assert learned["version"] == "learned-v1" and learned["active"] is False
        assert learned["predicted_lift"] > 0
        # No group has enough posts to be a proven winner, so nothing is steered.
        assert (pkg.post_tags or {})["strategy"] == "EXPLORE"


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


def test_bot_network_blip_is_not_a_failure(env: dict[str, Any]) -> None:
    from media_os_api.jobs.ledger import enqueue_durable_job
    from media_os_api.notify import commands
    from media_os_api.notify.telegram import TelegramError

    class _Flaky(_FakeBot):
        def get_updates(self, *, offset: int | None = None, limit: int = 50) -> list[Any]:
            raise TelegramError("timed out", code="TELEGRAM_TIMEOUT", retryable=True)

    f = env["factory"]
    try:
        with session_scope(f) as db:
            job, _ = enqueue_durable_job(
                db,
                task_name=commands.COMMANDS_TASK,
                task_type="NOTIFY",
                payload={},
                idempotency_key="test:commands:blip",
            )
            out = commands.handle_telegram_commands(db, job, telegram=lambda: _Flaky([]))  # type: ignore[arg-type,return-value]
        assert out["skipped"] == "TELEGRAM_TIMEOUT" and out["handled"] == 0
    finally:
        with session_scope(f) as db:
            ids = select(JobRun.id).where(JobRun.task_name == commands.COMMANDS_TASK)
            db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(ids)))
            db.execute(delete(JobRun).where(JobRun.task_name == commands.COMMANDS_TASK))


# ------------------------------------------------------------------ owner reels (Studio)

BRAND_Q = "brand=brand-app-promo"


def _upload(c: TestClient, data: bytes, name: str = "Wedding reel.mp4") -> Any:
    return c.post(
        f"/api/v1/content/reels/upload?{BRAND_Q}&filename={name}",
        content=data,
        headers={"Content-Type": "video/mp4"},
    )


def test_owner_reel_upload_approve_batch_schedule_and_publish(
    env: dict[str, Any], client: TestClient
) -> None:
    from tests.mp4_fixtures import make_mp4

    first = _upload(client, make_mp4(15))
    second = _upload(client, make_mp4(20), "Second.mp4")
    assert first.status_code == 201 and second.status_code == 201, first.text
    reel = first.json()
    assert reel["format"] == "REEL" and reel["status"] == "QA_PASSED"
    assert reel["approval_status"] == "PENDING" and reel["title"] == "Wedding reel"
    assert reel["duration_seconds"] == pytest.approx(15) and reel["video_asset_id"]

    # The video plays back from the uploads volume, with Range support.
    vid = client.get(f"/api/v1/content/assets/{reel['video_asset_id']}/file?{BRAND_Q}")
    assert vid.status_code == 200 and vid.headers["content-type"] == "video/mp4"
    part = client.get(
        f"/api/v1/content/assets/{reel['video_asset_id']}/file?{BRAND_Q}",
        headers={"Range": "bytes=0-7"},
    )
    assert part.status_code == 206 and len(part.content) == 8

    # Cover frame becomes the thumbnail.
    cover = client.put(
        f"/api/v1/content/reels/{reel['id']}/poster?{BRAND_Q}",
        content=b"\xff\xd8\xff\xe0" + b"0" * 100,
        headers={"Content-Type": "image/jpeg"},
    )
    assert cover.status_code == 200 and cover.json()["thumbnail_asset_id"]

    edit = client.patch(
        f"/api/v1/content/studio/{reel['id']}?{BRAND_Q}",
        json={"caption": "Our new card", "hashtags": ["biye", "#Nimontron"]},
    )
    assert edit.status_code == 200 and edit.json()["hashtags"] == ["#biye", "#Nimontron"]

    listed = client.get(f"/api/v1/content/studio?{BRAND_Q}").json()["items"]
    assert {r["id"] for r in listed} == {reel["id"], second.json()["id"]}
    # Owner reels are reviewed in Studio, not in the carousel review queue.
    review = client.get(f"/api/v1/content/carousels?{BRAND_Q}&queue=review").json()["items"]
    assert reel["id"] not in {r["id"] for r in review}

    for pid in (reel["id"], second.json()["id"]):
        ok = client.post(
            f"/api/v1/content/carousels/{pid}/decision?{BRAND_Q}", json={"decision": "APPROVE"}
        )
        assert ok.status_code == 200
    # After approval the caption is locked.
    assert (
        client.patch(f"/api/v1/content/studio/{reel['id']}?{BRAND_Q}", json={"caption": "x"})
    ).status_code == 409
    publish_q = client.get(f"/api/v1/content/carousels?{BRAND_Q}&queue=publish").json()["items"]
    assert reel["id"] in {r["id"] for r in publish_q}

    # Batch: the env caps each platform at 1 post a day, so the second reel on the same day is
    # refused with a reason while the first is scheduled. Midday IST, so +30 min stays that day.
    when = _when().astimezone(ZoneInfo("Asia/Kolkata")).replace(hour=12, minute=0)
    batch = client.post(
        f"/api/v1/content/schedule-batch?{BRAND_Q}",
        json={
            "platforms": ["INSTAGRAM", "FACEBOOK"],
            "items": [
                {"package_id": reel["id"], "scheduled_at": when.isoformat()},
                {
                    "package_id": second.json()["id"],
                    "scheduled_at": (when + timedelta(minutes=30)).isoformat(),
                },
            ],
        },
    )
    assert batch.status_code == 200, batch.text
    out = batch.json()
    assert out["scheduled"] == 1
    assert [r["ok"] for r in out["results"]] == [True, False]
    assert out["results"][1]["code"] == "DAILY_CAP"

    # The worker hosts the video itself (not slides) and hands Postiz an .mp4 URL.
    assert _run_job(env, when)["status"] == "SCHEDULED"
    host, postiz = env["host"], env["postiz"]
    (path, data) = host.published[-1]
    assert path.endswith("/reel.mp4") and isinstance(data, Path) and data.is_file()
    assert all(c["images"] == [f"https://o.github.io/m/{path}"] for c in postiz.created)
    assert (
        "Our new card" in postiz.created[0]["caption"] and "#biye" in postiz.created[0]["caption"]
    )

    # A scheduled reel can't be removed; an unscheduled one can, and its file goes.
    assert client.delete(f"/api/v1/content/studio/{reel['id']}?{BRAND_Q}").status_code == 409
    gone = client.delete(f"/api/v1/content/studio/{second.json()['id']}?{BRAND_Q}")
    assert gone.status_code == 204
    left = client.get(f"/api/v1/content/studio?{BRAND_Q}").json()["items"]
    assert [r["id"] for r in left] == [reel["id"]]
    with session_scope(env["factory"]) as db:
        removed = db.execute(
            select(Asset).where(
                Asset.content_package_id == uuid.UUID(second.json()["id"]),
                Asset.asset_type == "REEL_VIDEO",
            )
        ).scalar_one()
        assert removed.status == "DELETED"
        from media_os_api.content.uploads import resolve_asset_path

        p = resolve_asset_path(removed.local_path)
        assert p is not None and not p.exists()


def test_bad_uploads_are_refused_or_flagged(env: dict[str, Any], client: TestClient) -> None:
    from tests.mp4_fixtures import make_mp4

    not_video = _upload(client, b"\x89PNG\r\n\x1a\n" + b"0" * 100, "x.mp4")
    assert not_video.status_code == 201
    flagged = not_video.json()
    # Kept so the owner sees why, but it can never be approved.
    assert flagged["qa_passed"] is False and flagged["status"] == "RENDERED"
    refused = client.post(
        f"/api/v1/content/carousels/{flagged['id']}/decision?{BRAND_Q}",
        json={"decision": "APPROVE"},
    )
    assert refused.status_code == 409
    short = _upload(client, make_mp4(1))
    assert short.json()["qa_passed"] is False
    too_big = client.post(
        f"/api/v1/content/reels/upload?{BRAND_Q}",
        content=b"0" * 10,
        headers={"Content-Type": "video/mp4", "Content-Length": str(200 * 1024 * 1024)},
    )
    assert too_big.status_code == 413


def test_brand_logo_set_shown_and_removed(env: dict[str, Any], client: TestClient) -> None:
    png = _png()
    r = client.put(
        "/api/v1/brands/brand-app-promo/logo", content=png, headers={"Content-Type": "image/png"}
    )
    assert r.status_code == 200
    logo_id = r.json()["logo_asset_id"]
    brand = client.get("/api/v1/brands/brand-app-promo").json()
    assert brand["visual_identity"]["logo_asset_id"] == logo_id
    img = client.get(f"/api/v1/content/assets/{logo_id}/file?{BRAND_Q}")
    assert img.status_code == 200 and img.content == png
    lib = client.get(f"/api/v1/content/library?{BRAND_Q}&kind=BRAND_LOGO").json()["items"]
    assert [i["asset_id"] for i in lib] == [logo_id]

    # Replacing keeps one logo; the old file is retired.
    r2 = client.put(
        "/api/v1/brands/brand-app-promo/logo", content=png, headers={"Content-Type": "image/png"}
    )
    assert r2.json()["logo_asset_id"] != logo_id
    assert client.get(f"/api/v1/content/assets/{logo_id}/file?{BRAND_Q}").status_code == 404
    bad = client.put(
        "/api/v1/brands/brand-app-promo/logo",
        content=b"GIF89a",
        headers={"Content-Type": "image/gif"},
    )
    assert bad.status_code == 422
    assert client.delete("/api/v1/brands/brand-app-promo/logo").json() == {"logo_asset_id": None}
    brand = client.get("/api/v1/brands/brand-app-promo").json()
    assert "logo_asset_id" not in brand["visual_identity"]


def test_sync_now_queues_one_forced_analytics_run(env: dict[str, Any], client: TestClient) -> None:
    a = client.post(f"/api/v1/analytics/sync?{BRAND_Q}")
    b = client.post(f"/api/v1/analytics/sync?{BRAND_Q}")
    assert a.status_code == 202 and b.status_code == 202
    assert (
        a.json()["job_id"] == b.json()["job_id"]
        or a.json()["queued_at"][:16] != b.json()["queued_at"][:16]
    )
    with session_scope(env["factory"]) as db:
        job = db.get(JobRun, uuid.UUID(a.json()["job_id"]))
        assert job is not None and job.payload["force_recent"] is True
        assert job.queue_name == "research"
    assert set(client.get("/api/v1/analytics/sync").json()) >= {"last_success_at", "last_status"}


# ------------------------------------------------------------------ owner photo posts & carousels


def _jpeg(w: int = 1080, h: int = 1350, color: tuple[int, int, int] = (20, 90, 160)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def _stage(c: TestClient, data: bytes, mime: str = "image/jpeg") -> str:
    r = c.post(
        f"/api/v1/content/images/upload?{BRAND_Q}", content=data, headers={"Content-Type": mime}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["asset_id"])


def test_owner_carousel_keeps_the_chosen_order_and_publishes(
    env: dict[str, Any], client: TestClient
) -> None:
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200)]
    ids = [_stage(client, _jpeg(color=c)) for c in colors]
    ids.append(_stage(client, _png(), "image/png"))  # mixed formats are fine
    order = [ids[2], ids[0], ids[3], ids[1]]
    made = client.post(
        f"/api/v1/content/posts?{BRAND_Q}", json={"asset_ids": order, "title": "Menu cards"}
    )
    assert made.status_code == 201, made.text
    post = made.json()
    assert post["format"] == "CAROUSEL" and post["status"] == "QA_PASSED"
    assert post["slide_asset_ids"] == order and post["thumbnail_asset_id"] == order[0]
    assert post["title"] == "Menu cards" and post["slide_count"] == 4
    # Staged images are used up: they can't go into a second post.
    again = client.post(f"/api/v1/content/posts?{BRAND_Q}", json={"asset_ids": [ids[0]]})
    assert again.status_code == 409

    # Reviewed in Studio, not in the generated-carousel review queue.
    review = client.get(f"/api/v1/content/carousels?{BRAND_Q}&queue=review").json()["items"]
    assert post["id"] not in {r["id"] for r in review}
    studio = client.get(f"/api/v1/content/studio?{BRAND_Q}").json()["items"]
    assert post["id"] in {r["id"] for r in studio}

    client.patch(f"/api/v1/content/studio/{post['id']}?{BRAND_Q}", json={"caption": "New menus"})
    ok = client.post(
        f"/api/v1/content/carousels/{post['id']}/decision?{BRAND_Q}", json={"decision": "APPROVE"}
    )
    assert ok.status_code == 200
    when = _when()
    assert _api_schedule(client, uuid.UUID(post["id"]), when).status_code == 200
    assert _run_job(env, when)["status"] == "SCHEDULED"

    # Hosted as JPEGs in the chosen order.
    from PIL import Image

    hosted = env["host"].published[-4:]
    assert [p.rsplit("/", 1)[1] for p, _ in hosted] == [
        "slide-01.jpg",
        "slide-02.jpg",
        "slide-03.jpg",
        "slide-04.jpg",
    ]
    first = Image.open(io.BytesIO(hosted[0][1])).convert("RGB").getpixel((5, 5))
    assert isinstance(first, tuple) and first[2] > 150  # the blue image went first
    assert all(d[:3] == b"\xff\xd8\xff" for _, d in hosted)
    assert len(env["postiz"].created[-1]["images"]) == 4


def test_owner_single_photo_post_and_bad_images(env: dict[str, Any], client: TestClient) -> None:
    one = _stage(client, _jpeg(1080, 1080))
    made = client.post(f"/api/v1/content/posts?{BRAND_Q}", json={"asset_ids": [one]})
    assert made.status_code == 201
    post = made.json()
    assert post["format"] == "POST" and post["qa_passed"] is True

    client.post(
        f"/api/v1/content/carousels/{post['id']}/decision?{BRAND_Q}", json={"decision": "APPROVE"}
    )
    when = _when()
    assert _api_schedule(client, uuid.UUID(post["id"]), when).status_code == 200
    assert _run_job(env, when)["status"] == "SCHEDULED"
    assert len(env["postiz"].created[-1]["images"]) == 1

    # A very tall image is allowed but flagged (Instagram crops it).
    tall = _stage(client, _jpeg(600, 1600))
    flagged = client.post(f"/api/v1/content/posts?{BRAND_Q}", json={"asset_ids": [tall]}).json()
    assert flagged["qa_passed"] is True
    assert any(c["name"] == "aspect_ratio" for c in flagged["qa_report"]["checks"])

    bad = client.post(
        f"/api/v1/content/images/upload?{BRAND_Q}",
        content=b"GIF89a....",
        headers={"Content-Type": "image/gif"},
    )
    assert bad.status_code == 422
    eleven = [_stage(client, _jpeg(200, 250)) for _ in range(11)]
    too_many = client.post(f"/api/v1/content/posts?{BRAND_Q}", json={"asset_ids": eleven})
    assert too_many.status_code == 422

    # Removing deletes the uploaded files.
    removed = client.delete(f"/api/v1/content/studio/{flagged['id']}?{BRAND_Q}")
    assert removed.status_code == 204
    with session_scope(env["factory"]) as db:
        slide = db.execute(
            select(Asset).where(Asset.content_package_id == uuid.UUID(flagged["id"]))
        ).scalar_one()
        from media_os_api.content.uploads import resolve_asset_path

        path = resolve_asset_path(slide.local_path)
        assert slide.status == "DELETED" and path is not None and not path.exists()


# ------------------------------------------------------------------ whole-account statistics


def test_account_totals_are_stored_daily_served_and_reported(
    env: dict[str, Any], client: TestClient
) -> None:
    from media_os_api.analytics import account as acct
    from media_os_api.db.models import AccountMetricSnapshot

    f, when = env["factory"], _when()
    with session_scope(f) as db:  # scheduling once mirrors the Postiz channels into Media OS
        svc.handle_schedule_carousel(
            db, _schedule(db, env, when, ["INSTAGRAM", "FACEBOOK"]), clients=env["clients"]
        )
    now = datetime.now(UTC)
    try:
        for _ in range(2):  # a second read the same day replaces, never duplicates
            with session_scope(f) as db:
                out = acct.handle_sync_account_metrics(
                    db,
                    JobRun(task_name=acct.ACCOUNT_TASK, payload={}),
                    clients=env["clients"],
                    now=now,
                )
        assert out["channels"] == 2 and out["errors"] == []
        with session_scope(f) as db:
            rows = list(db.execute(select(AccountMetricSnapshot)).scalars())
            # IG: views, likes, reach × 2 windows; FB: views, engagements × 2 windows
            assert len(rows) == 10
            ig7 = {
                r.metric_key: float(r.value)
                for r in rows
                if r.platform == "INSTAGRAM" and r.window_days == 7
            }
            assert ig7 == {"views": 700.0, "likes": 7.0, "reach_daily_sum": 5.0}

        week = client.get(f"/api/v1/analytics/account?{BRAND_Q}&window=7").json()
        by_platform = {c["platform"]: c for c in week["channels"]}
        ig_display = {d["key"]: d["value"] for d in by_platform["INSTAGRAM"]["display"]}
        assert ig_display["views"] == 700.0 and ig_display["likes"] == 7.0
        assert ig_display["new_followers"] is None  # not reported: shown as a dash, never zero
        assert "reach_daily_sum" not in ig_display  # summed daily reach isn't shown as reach
        assert by_platform["FACEBOOK"]["metrics"]["views"] == 10.0
        assert week["combined"]["views"] == 710.0
        month = client.get(f"/api/v1/analytics/account?{BRAND_Q}&window=30").json()
        assert {c["platform"]: c for c in month["channels"]}["INSTAGRAM"]["metrics"][
            "views"
        ] == 3000.0

        # Sync now queues the account read too.
        client.post(f"/api/v1/analytics/sync?{BRAND_Q}")
        with session_scope(f) as db:
            queued = db.execute(
                select(JobRun).where(JobRun.task_name == acct.ACCOUNT_TASK)
            ).scalar_one()
            assert queued.payload["trigger"] == "manual"

        # The 10 PM report carries the whole-account section.
        with session_scope(f) as db:
            notify.handle_daily_digest(
                db, JobRun(id=uuid.uuid4(), task_name=notify.DIGEST_TASK, payload={}), now=now
            )
        (alert,) = _alerts(env, "digest")
        text = alert.payload["text"]
        assert "Whole account · last 7 days" in text and "Views 700" in text
        assert "Media views 10" in text
    finally:
        with session_scope(f) as db:
            db.execute(delete(AccountMetricSnapshot))
            jobs = select(JobRun.id).where(
                JobRun.task_name == "media_os.analytics.sync_account_metrics"
            )
            db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(jobs)))
            db.execute(
                delete(JobRun).where(JobRun.task_name == "media_os.analytics.sync_account_metrics")
            )


# ------------------------------------------------------------------ restart Postiz (dashboard)


def test_restart_postiz_request_is_recorded_once_and_reported(
    env: dict[str, Any], client: TestClient
) -> None:
    from media_os_api.db.models import OpsRequest

    try:
        assert client.get("/api/v1/system/postiz/restart").json()["status"] is None
        first = client.post("/api/v1/system/postiz/restart")
        assert first.status_code == 202 and first.json()["status"] == "PENDING"
        # A second click while it's waiting reuses the same request.
        again = client.post("/api/v1/system/postiz/restart")
        assert again.json()["id"] == first.json()["id"]
        with session_scope(env["factory"]) as db:
            rows = list(db.execute(select(OpsRequest)).scalars())
            assert len(rows) == 1 and rows[0].action == "RESTART_POSTIZ"
            # What the Windows helper does once Docker has restarted the container.
            rows[0].status, rows[0].finished_at = "DONE", datetime.now(UTC)
            rows[0].requested_at = datetime.now(UTC) - timedelta(minutes=10)
            rows[0].result_detail = "Postiz restarted."
        done = client.get("/api/v1/system/postiz/restart").json()
        assert done["status"] == "DONE" and done["detail"] == "Postiz restarted."
        # Straight after a restart, another one is refused.
        soon = client.post("/api/v1/system/postiz/restart")
        assert soon.status_code == 409 and soon.json()["error"]["code"] == "JUST_RESTARTED"
        # A request nobody picks up is flagged so the page can say so.
        with session_scope(env["factory"]) as db:
            db.add(
                OpsRequest(
                    action="RESTART_POSTIZ",
                    status="PENDING",
                    requested_by="owner:t",
                    requested_at=datetime.now(UTC) - timedelta(minutes=5),
                )
            )
        assert client.get("/api/v1/system/postiz/restart").json()["helper_late"] is True
    finally:
        with session_scope(env["factory"]) as db:
            db.execute(delete(OpsRequest))
            db.execute(delete(AuditEvent).where(AuditEvent.action == "POSTIZ_RESTART_REQUESTED"))


# ------------------------------------------------------------------ autopilot


def _fake_produce(calls: list[Any], seen_guidance: list[str] | None = None) -> Any:
    """Stands in for the AI writer: saves a real draft package, exactly like produce() does."""

    def produce(
        brand_slug: str,
        brief: Any,
        *,
        save: bool,
        factory: Any = None,
        design: dict[str, Any] | None = None,
        recent: Any = None,
        guidance: str = "",
    ) -> dict[str, Any]:
        calls.append(brief)
        if seen_guidance is not None:
            seen_guidance.append(guidance)
        draft = _draft()
        qa = run_text_qa(NIMONTRON, brief, draft)
        with session_scope(factory) as db:
            pkg = create_carousel_package(
                db,
                brand_slug=brand_slug,
                brief=brief,
                draft=draft,
                qa=qa,
                score={"total": 70},
                written_by="test/fake-model",
                design=design,
            )
            pid = str(pkg.id)
        return {
            "qa": qa.to_json(),
            "draft": draft.model_dump(mode="json"),
            "written_by": "test/fake-model",
            "content_package_id": pid,
        }

    return produce


def test_generate_button_queues_jobs_that_write_drafts_and_ping_telegram(
    env: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_os_api.carousel import admin
    from media_os_api.content import autopilot as ap

    opts = client.get(f"/api/v1/content/generate/options?{BRAND_Q}").json()
    assert opts["can_generate"] is True and opts["generated_today"] == 0
    assert {p["value"] for p in opts["pillars"]} == set(PILLARS)

    bad = client.post(f"/api/v1/content/generate?{BRAND_Q}", json={"pillar": "astrology"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "BAD_PILLAR"
    ok = client.post(
        f"/api/v1/content/generate?{BRAND_Q}",
        json={"count": 2, "pillar": "how_it_works", "language": "BN_EN"},
    )
    assert ok.status_code == 202 and ok.json()["queued"] == 2

    calls: list[Any] = []
    monkeypatch.setattr(admin, "produce", _fake_produce(calls))
    with session_scope(env["factory"]) as db:
        jobs = list(
            db.execute(select(JobRun).where(JobRun.task_name == ap.GENERATE_TASK)).scalars()
        )
        assert len(jobs) == 2 and all(j.queue_name == "research" for j in jobs)
        outs = [ap.handle_generate_carousel(db, j) for j in jobs]
        for j, out in zip(jobs, outs, strict=True):
            j.status, j.result_payload = "SUCCEEDED", out
    assert all(b.pillar == "how_it_works" and b.language.value == "BN_EN" for b in calls)
    assert all(o["qa_passed"] for o in outs)
    with session_scope(env["factory"]) as db:
        made = list(
            db.execute(
                select(ContentPackage).where(
                    ContentPackage.id.in_([uuid.UUID(o["content_package_id"]) for o in outs])
                )
            ).scalars()
        )
        assert len(made) == 2 and all(p.approval_status == "PENDING" for p in made)
    alerts = _alerts(env, "draft")
    assert len(alerts) == 2 and "New post ready for review" in alerts[0].payload["text"]
    recent = client.get(f"/api/v1/content/generate/recent?{BRAND_Q}").json()["items"]
    assert len(recent) == 2 and all(r["qa_passed"] for r in recent)


def test_rejection_notes_reach_the_next_draft(
    env: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_os_api.carousel import admin
    from media_os_api.content import autopilot as ap

    calls: list[Any] = []
    guidance: list[str] = []
    monkeypatch.setattr(admin, "produce", _fake_produce(calls, guidance))
    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        [job] = ap.request_generation(
            db, brand, count=1, brief={}, trigger="test", key_prefix="test:notes:1"
        )
        ap.handle_generate_carousel(db, job)
        job.status = "SUCCEEDED"
    pid = next(
        r["id"]
        for r in client.get(f"/api/v1/content/carousels?{BRAND_Q}").json()["items"]
        if r["approval_status"] == "PENDING"
    )
    rejected = client.post(
        f"/api/v1/content/carousels/{pid}/decision?{BRAND_Q}",
        json={"decision": "REJECT", "reason": "Too salesy, sounds like an ad"},
    )
    assert rejected.status_code == 200
    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        [job] = ap.request_generation(
            db, brand, count=1, brief={}, trigger="test", key_prefix="test:notes:2"
        )
        ap.handle_generate_carousel(db, job)
        job.status = "SUCCEEDED"
    assert guidance[0] == ""
    assert "Too salesy, sounds like an ad" in guidance[1]
    assert "rejected: Too salesy" in guidance[1]


def _pending_carousel(env: dict[str, Any], client: TestClient, monkeypatch: Any, key: str) -> str:
    """A fresh generated carousel awaiting review (written by the fake writer)."""
    from media_os_api.carousel import admin
    from media_os_api.content import autopilot as ap

    monkeypatch.setattr(admin, "produce", _fake_produce([]))
    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        [job] = ap.request_generation(
            db, brand, count=1, brief={"language": "BN_EN"}, trigger="test", key_prefix=key
        )
        out = ap.handle_generate_carousel(db, job)
        job.status = "SUCCEEDED"
    return str(out["content_package_id"])


def test_owner_edits_text_then_it_rechecks_rerenders_and_teaches(
    env: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_os_api.carousel.feedback import notes_block, owner_notes
    from media_os_api.carousel.render_job import RENDER_TASK

    pid = _pending_carousel(env, client, monkeypatch, "test:edit:1")
    url = f"/api/v1/content/carousels/{pid}?{BRAND_Q}"
    before = client.get(url).json()
    assert before["status"] == "DRAFTED"

    # Fill in the first slide's English headline only; other slides untouched.
    edits: list[dict[str, Any]] = [{} for _ in before["slides"]]
    edits[0] = {"headline_en": "Wedding *season* is here"}
    res = client.patch(url, json={"slides": edits, "caption": before["caption"] + " 🎉"})
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["slides"][0]["headline_en"] == "Wedding *season* is here"
    assert after["slides"][1] == before["slides"][1]
    assert after["title"] == "Wedding season is here"  # stored without the markers
    assert after["caption"].endswith("🎉") and after["qa_passed"] is True

    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, uuid.UUID(pid))
        assert pkg is not None and pkg.version == 2 and pkg.status == "DRAFTED"
        renders = list(
            db.execute(
                select(JobRun.idempotency_key).where(
                    JobRun.task_name == RENDER_TASK,
                    JobRun.payload["content_package_id"].astext == pid,
                )
            ).scalars()
        )
        assert f"render:{pid}:v2" in renders
        notes = owner_notes(db, env["bid"])
        assert (
            notes
            and "corrected it" in notes[0].note
            and "Wedding season is here" in (notes[0].note)
        )
        assert "the corrections show" in notes_block(notes)

    # Nothing changed: no new version.
    same = client.patch(url, json={"slides": edits})
    assert same.status_code == 200
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, uuid.UUID(pid))
        assert pkg is not None and pkg.version == 2

    # An edit that breaks a rule is saved but fails QA, so it isn't rendered.
    edits[0] = {"headline_en": "The best wedding card app"}
    bad = client.patch(url, json={"slides": edits})
    assert bad.status_code == 200 and bad.json()["qa_passed"] is False
    with session_scope(env["factory"]) as db:
        renders = list(
            db.execute(
                select(JobRun.idempotency_key).where(
                    JobRun.task_name == RENDER_TASK,
                    JobRun.payload["content_package_id"].astext == pid,
                )
            ).scalars()
        )
        assert f"render:{pid}:v3" not in renders

    wrong_count = client.patch(url, json={"slides": [{}]})
    assert wrong_count.status_code == 422 and wrong_count.json()["error"]["code"] == "BAD_EDIT"


def test_approved_carousels_cant_be_edited(env: dict[str, Any], client: TestClient) -> None:
    url = f"/api/v1/content/carousels/{env['pid']}?{BRAND_Q}"
    res = client.patch(url, json={"caption": "changed"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "NOT_EDITABLE"


def test_automatic_topics_rotate_away_from_recent_posts(env: dict[str, Any]) -> None:
    from media_os_api.content import autopilot as ap

    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        brief = ap.pick_brief(db, brand)
        # The env's only carousel is a wedding_season post: the next pick is a different topic.
        assert brief.pillar != "wedding_season" and brief.pillar in PILLARS
        picks = {ap.pick_brief(db, brand, seed=i).pillar for i in range(5)}
        assert len(picks) >= 2
        seasonal = ap.pick_brief(db, brand, pillar="how_it_works", hook="SEASONAL")
        assert seasonal.hook_type.value == "QUESTION"  # seasonal needs an occasion


def test_daily_drafts_queue_once_per_day_and_respect_off(
    env: dict[str, Any], client: TestClient
) -> None:
    from media_os_api.content import autopilot as ap

    now = datetime.now(UTC)
    for _ in range(2):  # a second run the same day doesn't add more
        with session_scope(env["factory"]) as db:
            out = ap.handle_daily_drafts(db, JobRun(task_name=ap.DAILY_TASK, payload={}), now=now)
    assert out["queued"].get("brand-app-promo") == 1
    with session_scope(env["factory"]) as db:
        n = db.execute(
            select(func.count()).select_from(JobRun).where(JobRun.task_name == ap.GENERATE_TASK)
        ).scalar_one()
        assert n == 1
    put = client.put(
        "/api/v1/brands/brand-app-promo/autopilot",
        json={"daily_drafts": 0, "auto_schedule": False, "post_times": ["19:00"]},
    )
    assert put.status_code == 200 and put.json()["daily_drafts"] == 0
    with session_scope(env["factory"]) as db:
        out = ap.handle_daily_drafts(
            db, JobRun(task_name=ap.DAILY_TASK, payload={}), now=now + timedelta(days=1)
        )
    assert "brand-app-promo" not in out["queued"]


def test_auto_schedule_places_approved_posts_into_free_posting_times(
    env: dict[str, Any], client: TestClient
) -> None:
    from media_os_api.content import autopilot as ap

    bad = client.put(
        "/api/v1/brands/brand-app-promo/autopilot",
        json={"daily_drafts": 1, "auto_schedule": True, "post_times": ["7pm"]},
    )
    assert bad.status_code == 422
    # Turning it on places the env's approved carousel straight away.
    on = client.put(
        "/api/v1/brands/brand-app-promo/autopilot",
        json={"daily_drafts": 1, "auto_schedule": True, "post_times": ["19:00"]},
    )
    assert on.status_code == 200 and on.json()["auto_schedule"] is True
    with session_scope(env["factory"]) as db:
        first = db.execute(select(JobRun).where(JobRun.task_name == svc.SCHEDULE_TASK)).scalar_one()
        at = datetime.fromisoformat(first.payload["scheduled_at"]).astimezone(
            ZoneInfo("Asia/Kolkata")
        )
        assert (at.hour, at.minute) == (19, 0)

        # A second approved carousel takes the next free 7 PM (the env caps each day at 1 post).
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        other = ContentPackage(
            brand_profile_id=brand.id,
            platform="INSTAGRAM",
            format="CAROUSEL",
            status="QA_PASSED",
            approval_status="PENDING",
            hashtags=[],
            slide_data=[],
            source_manifest=[],
            post_tags={"pillar": "how_it_works"},
        )
        db.add(other)
        db.flush()
        oid = other.id
    approved = client.post(
        f"/api/v1/content/carousels/{oid}/decision?{BRAND_Q}", json={"decision": "APPROVE"}
    )
    assert approved.status_code == 200 and approved.json()["scheduling"] is True
    with session_scope(env["factory"]) as db:
        jobs = list(
            db.execute(
                select(JobRun)
                .where(JobRun.task_name == svc.SCHEDULE_TASK)
                .order_by(JobRun.created_at)
            ).scalars()
        )
        times = [datetime.fromisoformat(j.payload["scheduled_at"]) for j in jobs]
        assert len(times) == 2 and (times[1] - times[0]) == timedelta(days=1)
        assert all(j.payload["platforms"] == ["INSTAGRAM", "FACEBOOK"] for j in jobs)
        # Owner uploads are never auto-scheduled.
        reel = ContentPackage(
            brand_profile_id=env["bid"],
            platform="INSTAGRAM",
            format="REEL",
            status="QA_PASSED",
            approval_status="APPROVED",
            hashtags=[],
            slide_data=[],
            source_manifest=[],
            qa_report={"version": "owner-reel-1", "passed": True, "checks": []},
        )
        db.add(reel)
        db.flush()
        assert ap.auto_schedule(db, reel, brand) is None


# ------------------------------------------------------------------ design templates


def _bg(w: int = 600, h: int = 750, color: tuple[int, int, int] = (90, 20, 40)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_set(c: TestClient, name: str, occasions: list[str] | None = None) -> str:
    made = c.post(f"/api/v1/content/templates?{BRAND_Q}", json={"name": name})
    assert made.status_code == 201, made.text
    tid = str(made.json()["id"])
    for role in ("COVER", "CONTENT", "CTA"):
        r = c.put(
            f"/api/v1/content/templates/{tid}/images/{role}?{BRAND_Q}",
            content=_bg(),
            headers={"Content-Type": "image/png"},
        )
        assert r.status_code == 200, r.text
    if occasions is not None:
        c.patch(f"/api/v1/content/templates/{tid}?{BRAND_Q}", json={"occasions": occasions})
    return tid


def test_template_sets_are_created_checked_and_offered_in_generate(
    env: dict[str, Any], client: TestClient
) -> None:
    made = client.post(f"/api/v1/content/templates?{BRAND_Q}", json={"name": "Gold mandala"})
    tid = made.json()["id"]
    assert made.json()["ready"] is False
    wrong = client.put(
        f"/api/v1/content/templates/{tid}/images/COVER?{BRAND_Q}",
        content=_bg(800, 800),
        headers={"Content-Type": "image/png"},
    )
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "WRONG_SHAPE"
    for role in ("COVER", "CONTENT", "CTA"):
        client.put(
            f"/api/v1/content/templates/{tid}/images/{role}?{BRAND_Q}",
            content=_bg(),
            headers={"Content-Type": "image/png"},
        )
    patched = client.patch(
        f"/api/v1/content/templates/{tid}?{BRAND_Q}",
        json={
            "occasions": ["WEDDING_HINDU"],
            "slides": {"COVER": {"area": "bottom", "tone": "dark"}},
        },
    ).json()
    assert patched["ready"] is True and patched["occasions"] == ["WEDDING_HINDU"]
    assert patched["slides"]["COVER"] | {"asset_id": None} == {
        "asset_id": None,
        "width": 600,
        "height": 750,
        "area": "bottom",
        "tone": "dark",
    }
    bad = client.patch(
        f"/api/v1/content/templates/{tid}?{BRAND_Q}", json={"occasions": ["WEDDING_BUDDHIST"]}
    )
    assert bad.status_code == 422
    opts = client.get(f"/api/v1/content/generate/options?{BRAND_Q}").json()
    assert {"value": tid, "label": "Gold mandala"} in opts["designs"]
    listed = client.get(f"/api/v1/content/templates?{BRAND_Q}").json()["items"]
    assert [t["id"] for t in listed] == [tid]
    # Switched off: no longer offered.
    client.patch(f"/api/v1/content/templates/{tid}?{BRAND_Q}", json={"is_active": False})
    opts = client.get(f"/api/v1/content/generate/options?{BRAND_Q}").json()
    assert tid not in {d["value"] for d in opts["designs"]}
    assert client.delete(f"/api/v1/content/templates/{tid}?{BRAND_Q}").status_code == 204
    assert client.get(f"/api/v1/content/templates?{BRAND_Q}").json()["items"] == []


def test_generated_post_records_its_design_and_the_renderer_finds_it(
    env: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_os_api.carousel import admin
    from media_os_api.carousel.render_job import design_inputs
    from media_os_api.content import autopilot as ap

    tid = _make_set(client, "Minimal cream")
    ok = client.post(
        f"/api/v1/content/generate?{BRAND_Q}",
        json={"count": 1, "language": "BN_EN", "design": tid},
    )
    assert ok.status_code == 202, ok.text
    bad = client.post(
        f"/api/v1/content/generate?{BRAND_Q}",
        json={"count": 1, "design": "00000000-0000-0000-0000-000000000000"},
    )
    assert bad.status_code == 422
    calls: list[Any] = []

    monkeypatch.setattr(admin, "produce", _fake_produce(calls))
    with session_scope(env["factory"]) as db:
        job = db.execute(select(JobRun).where(JobRun.task_name == ap.GENERATE_TASK)).scalar_one()
        out = ap.handle_generate_carousel(db, job)
    assert out["design"] == "Minimal cream"
    (alert,) = _alerts(env, "draft")
    assert "Design: Minimal cream" in alert.payload["text"]
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, uuid.UUID(out["content_package_id"]))
        assert pkg is not None
        files, settings, missing = design_inputs(pkg)
        assert files is not None and set(files) == {"COVER", "CONTENT", "CTA"}
        assert all(p.is_file() for p in files.values()) and missing == []
        assert settings == {r: {"area": "middle", "tone": "light"} for r in files}


def test_media_os_chooses_designs_by_occasion_and_rotation(
    env: dict[str, Any], client: TestClient
) -> None:
    from media_os_api.content.designs import choose_design

    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        assert choose_design(db, brand, "auto", "BIRTHDAY") is None  # no sets: built-in
    wedding = _make_set(client, "Wedding red", ["WEDDING_HINDU"])
    everyday = _make_set(client, "Everyday")
    hidden = _make_set(client, "Manual only")
    client.patch(f"/api/v1/content/templates/{hidden}?{BRAND_Q}", json={"auto_allowed": False})
    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        # A set tagged for the occasion wins for that occasion.
        w = choose_design(db, brand, "auto", "WEDDING_HINDU")
        assert w is not None and w["template_id"] == wedding
        # Otherwise untagged sets rotate with the built-in design; manual-only sets never appear.
        picks = {
            (d or {}).get("template_id", "builtin")
            for d in [choose_design(db, brand, "auto", "BIRTHDAY")]
        }
        assert picks <= {everyday, "builtin"} and hidden not in picks
        assert choose_design(db, brand, "builtin", "WEDDING_HINDU") is None
        chosen = choose_design(db, brand, hidden, "BIRTHDAY")  # picked by hand: allowed
        assert chosen is not None and chosen["template_id"] == hidden


def test_posts_per_day_setting_changes_the_scheduling_limit(
    env: dict[str, Any], client: TestClient
) -> None:
    base = {"daily_drafts": 1, "auto_schedule": False, "post_times": ["19:00"]}
    start = client.get("/api/v1/brands/brand-app-promo/autopilot").json()
    assert start["daily_caps"] == {"INSTAGRAM": 1, "FACEBOOK": 1}  # the env's caps
    bad = client.put(
        "/api/v1/brands/brand-app-promo/autopilot",
        json={**base, "daily_caps": {"INSTAGRAM": 11, "FACEBOOK": 2}},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "BAD_DAILY_CAPS"
    ok = client.put(
        "/api/v1/brands/brand-app-promo/autopilot",
        json={**base, "daily_caps": {"INSTAGRAM": 3, "FACEBOOK": 2}},
    )
    assert ok.status_code == 200 and ok.json()["daily_caps"] == {"INSTAGRAM": 3, "FACEBOOK": 2}
    with session_scope(env["factory"]) as db:
        brand = db.get(BrandProfile, env["bid"])
        assert brand is not None
        caps = brand.publishing_preferences["daily_caps"]
        assert caps["INSTAGRAM"] == 3 and caps["FACEBOOK"] == 2
        # Scheduling uses the new limit: a second Instagram post the same day is now allowed.
        when = _when().astimezone(ZoneInfo("Asia/Kolkata")).replace(hour=12, minute=0)
        pkg = db.get(ContentPackage, env["pid"])
        assert pkg is not None
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
        svc.request_schedule(
            db,
            pkg=other,
            brand=brand,
            when=when + timedelta(hours=1),
            platforms=["INSTAGRAM"],
            actor_id="owner:t",
        )
