"""Integration (disposable DB): carousel save → render job → assets/QA → review API → decision."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from media_os_api.auth import create_redis_client, hash_password
from media_os_api.carousel import render as render_mod
from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.qa import run_text_qa
from media_os_api.carousel.render import RenderedSlide
from media_os_api.carousel.render_job import RENDER_TASK, handle_render_carousel
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft
from media_os_api.carousel.service import create_carousel_package
from media_os_api.config import load_settings
from media_os_api.content.taxonomy import HookType, Language
from media_os_api.db import (
    Asset,
    AuditEvent,
    AuthSession,
    BrandProfile,
    ContentPackage,
    ContentStateTransition,
    JobRun,
    OutboxEvent,
    OwnerUser,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.main import create_app

pytestmark = pytest.mark.integration

OWNER = "n2-owner"
PASSWORD = "N2-Strong-Test-Password!2026"
SECRET = "n2-test-session-secret-0123456789abcdef0123456789abcdef0123"
ORIGIN = "http://localhost:3000"
PILLARS = ["wedding_season", "how_it_works", "wording_help", "design_showcase", "other_occasions"]
PLAY = NIMONTRON.play_url


def _urls() -> tuple[str, str]:
    db_url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    if not db_url or not redis_url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL / MEDIA_OS_TEST_REDIS_URL not set.")
    return db_url, redis_url


def _draft() -> CarouselDraft:
    def s(role: str, bn: str, en: str, claims: list[str], asset: str | None = None) -> Any:
        return {
            "role": role,
            "headline_bn": bn,
            "headline_en": en,
            "claim_ids": claims,
            "visual_asset_id": asset,
        }

    return CarouselDraft.model_validate(
        {
            "slides": [
                s("HOOK", "বিয়ের মরশুম", "Wedding season", []),
                s("POINT", "তিন ধাপে কার্ড", "Three steps", ["speed"]),
                s("PROOF", "ইংরেজিতে নাম", "Names in English", ["names_bn"], "screen_names_bn"),
                s("POINT", "এক ট্যাপে পাঠান", "One tap to WhatsApp", ["sharing"]),
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
    db_url, redis_url = _urls()
    rc = create_redis_client(redis_url)
    for key in rc.scan_iter("media_os:rl:login:*"):
        rc.delete(key)
    rc.close()
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    with session_scope(factory) as db:
        db.execute(delete(AuthSession))
        db.execute(delete(OwnerUser))
        brand = db.execute(
            select(BrandProfile).where(BrandProfile.slug == "brand-app-promo")
        ).scalar_one()
        saved_pillars = list(brand.content_pillars)
        brand.content_pillars = PILLARS
    monkeypatch.setenv("MEDIA_OS_MEDIA_ROOT", str(tmp_path))
    calls: list[bool] = []

    def fake_render(
        kit: Any, slides: list[Any], language: Any, out_dir: Path, *, festive: bool = False
    ) -> list[Any]:
        calls.append(festive)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for i, _ in enumerate(slides, 1):
            png = out_dir / f"slide-{i:02d}.png"
            data = b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64
            png.write_bytes(data)
            out.append(
                RenderedSlide(i, png, 1080, 1350, len(data), hashlib.sha256(data).hexdigest(), [])
            )
        return out

    monkeypatch.setattr(render_mod, "render_carousel", fake_render)
    try:
        yield {
            "factory": factory,
            "db_url": db_url,
            "redis_url": redis_url,
            "root": tmp_path,
            "festive_calls": calls,
        }
    finally:
        with session_scope(factory) as db:
            ids = list(
                db.execute(
                    select(ContentPackage.id).where(ContentPackage.format == "CAROUSEL")
                ).scalars()
            )
            if ids:
                db.execute(delete(Asset).where(Asset.content_package_id.in_(ids)))
                db.execute(
                    delete(ContentStateTransition).where(
                        ContentStateTransition.content_package_id.in_(ids)
                    )
                )
                db.execute(delete(ContentPackage).where(ContentPackage.id.in_(ids)))
            jobs = select(JobRun.id).where(JobRun.task_name == RENDER_TASK)
            db.execute(delete(OutboxEvent).where(OutboxEvent.job_run_id.in_(jobs)))
            db.execute(delete(JobRun).where(JobRun.task_name == RENDER_TASK))
            db.execute(delete(AuditEvent).where(AuditEvent.action.like("CAROUSEL_%")))
            brand = db.execute(
                select(BrandProfile).where(BrandProfile.slug == "brand-app-promo")
            ).scalar_one()
            brand.content_pillars = saved_pillars
        engine.dispose()


def _save(factory: Any) -> str:
    brief = CarouselBrief(
        pillar="wedding_season", hook_type=HookType.SEASONAL, language=Language.BN_EN
    )
    draft = _draft()
    qa = run_text_qa(NIMONTRON, brief, draft)
    assert qa.passed, qa.failures
    with session_scope(factory) as db:
        pkg = create_carousel_package(
            db,
            brand_slug="brand-app-promo",
            brief=brief,
            draft=draft,
            qa=qa,
            score={"total": 80, "label": "Heuristic v0"},
            written_by="test/model",
        )
        return str(pkg.id)


def _render(factory: Any, package_id: str) -> dict[str, Any]:
    with session_scope(factory) as db:
        job = db.execute(select(JobRun).where(JobRun.task_name == RENDER_TASK)).scalars().first()
        assert job is not None and job.queue_name == "creative"
        assert job.payload == {"content_package_id": package_id}
        return handle_render_carousel(db, job)


def test_save_enqueues_render_and_render_reaches_qa_passed(env: dict[str, Any]) -> None:
    pid = _save(env["factory"])
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, pid)
        assert pkg is not None and pkg.status == "DRAFTED"
        assert pkg.post_tags and pkg.post_tags["language"] == "BN_EN"
        assert pkg.post_tags["slide_count"] == 5

    result = _render(env["factory"], pid)
    assert result == {"slides": 5, "qa_passed": True, "status": "QA_PASSED"}
    assert env["festive_calls"] == [True]  # wedding_season + SEASONAL hook gets the garland
    with session_scope(env["factory"]) as db:
        assets = list(db.execute(select(Asset).where(Asset.content_package_id == pid)).scalars())
        assert len(assets) == 5 and all(a.local_path.startswith("carousels/") for a in assets)
        pkg = db.get(ContentPackage, pid)
        assert pkg is not None
        names = {c["name"] for c in (pkg.qa_report or {})["checks"]}
        assert {"render_text_fits", "render_dimensions", "claims_approved"} <= names
        # Replay is a no-op (idempotent).
        job = db.execute(select(JobRun).where(JobRun.task_name == RENDER_TASK)).scalars().first()
        assert job is not None and handle_render_carousel(db, job)["skipped"] is True


@pytest.fixture
def client(env: dict[str, Any]) -> Iterator[TestClient]:
    settings = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": OWNER,
            "OWNER_PASSWORD_HASH": hash_password(PASSWORD),
            "SESSION_SECRET": SECRET,
            "COOKIE_SECURE": "false",
            "CORS_ALLOWED_ORIGINS": ORIGIN,
            "DATABASE_URL": env["db_url"],
            "REDIS_URL": env["redis_url"],
        }
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _login(c: TestClient) -> str:
    r = c.post(
        "/api/v1/auth/login",
        json={"username": OWNER, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 200
    return str(r.json()["csrf_token"])


def test_review_api_queue_files_and_decisions(env: dict[str, Any], client: TestClient) -> None:
    first = _save(env["factory"])
    _render(env["factory"], first)

    assert client.get("/api/v1/content/carousels?brand=brand-app-promo").status_code == 401
    csrf = _login(client)
    queue = client.get("/api/v1/content/carousels?brand=brand-app-promo").json()["items"]
    assert [i["id"] for i in queue] == [first]
    assert queue[0]["language"] == "BN_EN" and queue[0]["score_total"] == 80
    # Brand isolation: the tech brand sees nothing and cannot open it.
    assert client.get("/api/v1/content/carousels?brand=brand-tech-ai").json()["items"] == []
    assert client.get(f"/api/v1/content/carousels/{first}?brand=brand-tech-ai").status_code == 404

    detail = client.get(f"/api/v1/content/carousels/{first}?brand=brand-app-promo").json()
    assert len(detail["assets"]) == 5 and detail["caption"].count(PLAY) == 2
    png = client.get(
        f"/api/v1/content/assets/{detail['assets'][0]['id']}/file?brand=brand-app-promo"
    )
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert (
        client.get(
            f"/api/v1/content/assets/{detail['assets'][0]['id']}/file?brand=brand-tech-ai"
        ).status_code
        == 404
    )

    headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
    url = f"/api/v1/content/carousels/{first}/decision?brand=brand-app-promo"
    assert client.post(url, json={"decision": "APPROVE"}).status_code in (401, 403)  # no CSRF
    ok = client.post(url, json={"decision": "APPROVE"}, headers=headers)
    assert ok.status_code == 200 and ok.json()["approval_status"] == "APPROVED"
    assert client.post(url, json={"decision": "REJECT"}, headers=headers).status_code == 409
    assert client.get("/api/v1/content/carousels?brand=brand-app-promo").json()["items"] == []

    second = _save(env["factory"])  # DRAFTED (not rendered): approve refused, reject allowed
    url2 = f"/api/v1/content/carousels/{second}/decision?brand=brand-app-promo"
    assert client.post(url2, json={"decision": "APPROVE"}, headers=headers).status_code == 409
    rej = client.post(url2, json={"decision": "REJECT", "reason": "Hook too weak"}, headers=headers)
    assert rej.status_code == 200 and rej.json()["status"] == "REJECTED"
    with session_scope(env["factory"]) as db:
        actions = set(
            db.execute(
                select(AuditEvent.action).where(AuditEvent.action.like("CAROUSEL_%"))
            ).scalars()
        )
        assert {"CAROUSEL_DRAFTED", "CAROUSEL_APPROVED", "CAROUSEL_REJECTED"} <= actions


def test_failed_draft_recheck_renders_after_rules_change(env: dict[str, Any]) -> None:
    from media_os_api.carousel.service import RecheckError, recheck_package

    pid = _save(env["factory"])
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, pid)
        assert pkg is not None
        # Simulate a draft that failed an older, stricter rule (no render job yet).
        pkg.qa_report = {
            "passed": False,
            "checks": [
                {"name": "banned_phrases", "status": "FAIL", "detail": "testimonial: 'Review'"}
            ],
        }
        db.execute(
            delete(OutboxEvent).where(
                OutboxEvent.job_run_id.in_(select(JobRun.id).where(JobRun.task_name == RENDER_TASK))
            )
        )
        db.execute(delete(JobRun).where(JobRun.task_name == RENDER_TASK))
    with session_scope(env["factory"]) as db:
        pkg = db.get(ContentPackage, pid)
        assert pkg is not None
        qa = recheck_package(db, pkg, actor_id="owner:test")
        assert qa.passed and (pkg.qa_report or {}).get("passed") is True
        assert db.execute(select(JobRun).where(JobRun.task_name == RENDER_TASK)).first()
        with pytest.raises(RecheckError):
            recheck_package(db, pkg, actor_id="owner:test")  # already passed
