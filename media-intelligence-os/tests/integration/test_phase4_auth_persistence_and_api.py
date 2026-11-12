"""Integration tests for PostgreSQL auth persistence, rotation, Redis rate limiting, and APIs."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from media_os_api.auth import (
    cleanup_expired_auth_sessions,
    hash_password,
    rotate_owner_password,
    set_owner_active_status,
)
from media_os_api.config import load_settings
from media_os_api.db import (
    AuthSession,
    OwnerUser,
    create_db_engine,
    create_session_factory,
    ensure_owner_bootstrapped,
    session_scope,
)
from media_os_api.main import create_app
from media_os_api.schemas.system import PostizReachabilitySummary

pytestmark = pytest.mark.integration

TEST_OWNER = "phase4-owner"
TEST_PASS_1 = "Phase4-Strong-Password-One!2026"
TEST_PASS_2 = "Phase4-Rotated-Password-Two!2026"
TEST_SECRET = "phase4-test-session-secret-0123456789abcdef0123456789abcdef"


def _get_db_and_redis() -> tuple[str, str]:
    db_url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    if not db_url or not redis_url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL / MEDIA_OS_TEST_REDIS_URL not set.")
    return db_url, redis_url


def _make_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db_url: str,
    redis_url: str,
    password_plain: str = TEST_PASS_1,
    rate_limit_max: int = 3,
) -> TestClient:
    async def _fake_probe(
        base_url: str, *, timeout_seconds: float = 2.0
    ) -> PostizReachabilitySummary:
        return PostizReachabilitySummary(
            status="reachable",
            checked_at_utc="2026-11-10T00:00:00+00:00",
            detail="Mocked Postiz probe.",
        )

    monkeypatch.setattr(
        "media_os_api.api.routes.system.probe_postiz_reachability",
        _fake_probe,
    )
    settings = load_settings(
        {
            "APP_ENV": "test",
            "LOG_LEVEL": "INFO",
            "OWNER_USERNAME": TEST_OWNER,
            "OWNER_PASSWORD_HASH": hash_password(password_plain),
            "SESSION_SECRET": TEST_SECRET,
            "COOKIE_SECURE": "false",
            "CORS_ALLOWED_ORIGINS": "http://127.0.0.1:3000,http://localhost:3000",
            "LOGIN_RATE_LIMIT_MAX_ATTEMPTS": str(rate_limit_max),
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS": "120",
            "DATABASE_URL": db_url,
            "REDIS_URL": redis_url,
        }
    )
    return TestClient(create_app(settings))


def test_owner_bootstrap_duplicate_protection_and_restart_revocation_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url, redis_url = _get_db_and_redis()

    from media_os_api.auth import create_redis_client

    rclient = create_redis_client(redis_url)
    for key in rclient.scan_iter("media_os:rl:login:*"):
        rclient.delete(key)
    rclient.close()

    # Reset auth tables cleanly for deterministic test
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    with session_scope(factory) as db:
        db.query(AuthSession).delete()
        db.query(OwnerUser).delete()
    engine.dispose()

    # 1. Boot first API instance: provisions owner and issues session
    with _make_app(monkeypatch, db_url=db_url, redis_url=redis_url) as client1:
        ready = client1.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["persistent_storage"] is True

        login1 = client1.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
            headers={"Origin": "http://localhost:3000"},
        )
        assert login1.status_code == 200
        csrf1 = login1.json()["csrf_token"]
        cookie1 = client1.cookies.get("media_os_session")
        assert cookie1

        # Logout revokes session persistently
        out = client1.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf1, "Origin": "http://localhost:3000"},
        )
        assert out.status_code == 200

    # 2. Simulate full API process restart with a DIFFERENT env password hash!
    # Duplicate bootstrap MUST NOT silently overwrite persisted DB owner password.
    with _make_app(
        monkeypatch,
        db_url=db_url,
        redis_url=redis_url,
        password_plain="DifferentIgnoredRestartPass!999",
    ) as client2:
        # Stolen/revoked cookie from BEFORE API restart MUST remain rejected!
        client2.cookies.set("media_os_session", cookie1)
        replay = client2.get("/api/v1/auth/me")
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "SESSION_INVALID"
        client2.cookies.clear()

        # Original persisted DB password still works; ignored env password does not!
        bad_env_login = client2.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": "DifferentIgnoredRestartPass!999"},
        )
        assert bad_env_login.status_code == 401

        good_login = client2.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
        )
        assert good_login.status_code == 200
        active_cookie = good_login.cookies.get("media_os_session") or client2.cookies.get(
            "media_os_session"
        )
        assert active_cookie

    # 3. Deliberate password rotation invalidates active sessions immediately (same second too)
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            # Verify ensure_owner_bootstrapped doesn't touch existing owner
            settings_check = load_settings(
                {
                    "APP_ENV": "test",
                    "OWNER_USERNAME": TEST_OWNER,
                    "OWNER_PASSWORD_HASH": hash_password("ShouldNotOverwrite!123"),
                    "SESSION_SECRET": TEST_SECRET,
                }
            )
            _owner, created = ensure_owner_bootstrapped(db, settings_check)
            assert created is False

            rotate_owner_password(db, username=TEST_OWNER, new_password=TEST_PASS_2)
    finally:
        engine.dispose()

    with _make_app(monkeypatch, db_url=db_url, redis_url=redis_url) as client3:
        client3.cookies.set("media_os_session", active_cookie)
        stale_me = client3.get("/api/v1/auth/me")
        assert stale_me.status_code == 401
        assert stale_me.json()["error"]["code"] == "SESSION_INVALID"
        client3.cookies.clear()

        old_pwd = client3.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
        )
        assert old_pwd.status_code == 401

        new_pwd = client3.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_2},
        )
        assert new_pwd.status_code == 200
        rot_cookie = new_pwd.cookies.get("media_os_session") or client3.cookies.get(
            "media_os_session"
        )
        assert rot_cookie

    # 4. Deactivating owner invalidates sessions and rejects logins
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            set_owner_active_status(db, username=TEST_OWNER, is_active=False)
    finally:
        engine.dispose()

    with _make_app(monkeypatch, db_url=db_url, redis_url=redis_url) as client4:
        client4.cookies.set("media_os_session", rot_cookie)
        assert client4.get("/api/v1/auth/me").status_code == 401
        assert (
            client4.post(
                "/api/v1/auth/login",
                json={"username": TEST_OWNER, "password": TEST_PASS_2},
            ).status_code
            == 401
        )

    # Restore owner active for subsequent tests and test expired session cleanup
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            owner = set_owner_active_status(db, username=TEST_OWNER, is_active=True)
            now = datetime.now(UTC)
            db.add(
                AuthSession(
                    owner_user_id=owner.id,
                    jti="expired-test-jti-1",
                    issued_at=now - timedelta(hours=10),
                    expires_at=now - timedelta(minutes=1),
                    revoked_at=now - timedelta(hours=9),
                    revocation_reason="logout",
                )
            )
            db.add(
                AuthSession(
                    owner_user_id=owner.id,
                    jti="unexpired-revoked-jti-1",
                    issued_at=now,
                    expires_at=now + timedelta(hours=4),
                    revoked_at=now,
                    revocation_reason="logout",
                )
            )
            db.flush()
            deleted = cleanup_expired_auth_sessions(db, now=now)
            assert deleted >= 1
            kept = db.execute(
                select(AuthSession).where(AuthSession.jti == "unexpired-revoked-jti-1")
            ).scalar_one_or_none()
            assert kept is not None
    finally:
        engine.dispose()


def test_redis_rate_limiting_persists_across_api_restart_and_brand_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url, redis_url = _get_db_and_redis()

    from media_os_api.auth import create_redis_client

    rclient = create_redis_client(redis_url)
    for key in rclient.scan_iter("media_os:rl:login:*"):
        rclient.delete(key)
    rclient.close()

    # Keep test 100% independent of test execution order
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    with session_scope(factory) as db:
        db.query(AuthSession).delete()
        db.query(OwnerUser).delete()
    engine.dispose()

    # Use 2 failed logins in client1, then "restart" API with client2 and fail once more -> 429!
    with _make_app(
        monkeypatch,
        db_url=db_url,
        redis_url=redis_url,
        rate_limit_max=3,
    ) as client1:
        for _ in range(2):
            resp = client1.post(
                "/api/v1/auth/login",
                json={"username": TEST_OWNER, "password": "wrong-pass-check"},
            )
            assert resp.status_code == 401

    with _make_app(
        monkeypatch,
        db_url=db_url,
        redis_url=redis_url,
        rate_limit_max=3,
    ) as client2:
        third = client2.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": "wrong-pass-check"},
        )
        assert third.status_code == 401
        fourth = client2.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": "wrong-pass-check"},
        )
        assert fourth.status_code == 429
        assert int(fourth.headers["Retry-After"]) >= 1

    # Clear RL keys and verify persisted Brand & Audit endpoints
    rclient = create_redis_client(redis_url)
    for key in rclient.scan_iter("media_os:rl:login:*"):
        rclient.delete(key)
    rclient.close()

    with _make_app(monkeypatch, db_url=db_url, redis_url=redis_url) as client3:
        login = client3.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
            headers={"Origin": "http://localhost:3000"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]

        brands = client3.get("/api/v1/brands")
        assert brands.status_code == 200
        b_data = brands.json()
        assert b_data["persistent"] is True
        assert b_data["is_prototype_fixture"] is False
        slugs = {b["slug"]: b for b in b_data["items"]}
        assert set(slugs.keys()) == {"brand-tech-ai", "brand-app-promo"}
        assert slugs["brand-app-promo"]["placeholder_unconfigured"] is True

        detail = client3.get("/api/v1/brands/brand-tech-ai")
        assert detail.status_code == 200
        assert detail.json()["slug"] == "brand-tech-ai"

        # Deactivate Brand 2 with CSRF, then refuse deactivating the last active Brand 1
        deact2 = client3.post(
            "/api/v1/brands/brand-app-promo/activation",
            json={"is_active": False},
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
        )
        assert deact2.status_code == 200
        assert deact2.json()["active"] is False

        deact1 = client3.post(
            "/api/v1/brands/brand-tech-ai/activation",
            json={"is_active": False},
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
        )
        assert deact1.status_code == 400
        assert deact1.json()["error"]["code"] == "BRAND_POLICY_ERROR"

        # Reactivate Brand 2 cleanly
        react2 = client3.post(
            "/api/v1/brands/brand-app-promo/activation",
            json={"is_active": True},
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
        )
        assert react2.status_code == 200
        assert react2.json()["active"] is True

        audit_resp = client3.get("/api/v1/system/audit-events")
        assert audit_resp.status_code == 200
        assert audit_resp.json()["persistent"] is True
        assert len(audit_resp.json()["items"]) >= 1
        assert TEST_PASS_2 not in str(audit_resp.json())


def test_auth_redis_outage_modes_and_db_recovery_preserve_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_url, redis_url = _get_db_and_redis()
    from redis.exceptions import ConnectionError as RedisConnectionError

    # 1. Fail-closed Redis outage -> HTTP 503 RATE_LIMITER_UNAVAILABLE
    settings_fc = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": TEST_OWNER,
            "OWNER_PASSWORD_HASH": hash_password(TEST_PASS_1),
            "SESSION_SECRET": TEST_SECRET,
            "COOKIE_SECURE": "false",
            "LOGIN_RATE_LIMIT_MAX_ATTEMPTS": "2",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS": "60",
            "RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE": "true",
            "DATABASE_URL": db_url,
            "REDIS_URL": redis_url,
        }
    )
    app_fc = create_app(settings_fc)
    with TestClient(app_fc) as client_fc:

        def _broken_pipeline(*_args: object, **_kwargs: object) -> object:
            raise RedisConnectionError("Simulated Redis outage")

        monkeypatch.setattr(
            app_fc.state.auth_state.rate_limiter._redis, "pipeline", _broken_pipeline
        )
        resp_503 = client_fc.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
        )
        assert resp_503.status_code == 503
        assert resp_503.json()["error"]["code"] == "RATE_LIMITER_UNAVAILABLE"

    # 2. Local fallback Redis outage -> bounded in-memory limiter still enforces 429
    settings_fb = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": TEST_OWNER,
            "OWNER_PASSWORD_HASH": hash_password(TEST_PASS_1),
            "SESSION_SECRET": TEST_SECRET,
            "COOKIE_SECURE": "false",
            "LOGIN_RATE_LIMIT_MAX_ATTEMPTS": "2",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS": "60",
            "RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE": "false",
            "DATABASE_URL": db_url,
            "REDIS_URL": redis_url,
        }
    )
    app_fb = create_app(settings_fb)
    with TestClient(app_fb) as client_fb:

        def _broken_pipeline_fb(*_args: object, **_kwargs: object) -> object:
            raise RedisConnectionError("Simulated Redis outage")

        monkeypatch.setattr(
            app_fb.state.auth_state.rate_limiter._redis,
            "pipeline",
            _broken_pipeline_fb,
        )
        for _ in range(2):
            assert (
                client_fb.post(
                    "/api/v1/auth/login",
                    json={"username": TEST_OWNER, "password": "bad-password-123"},
                ).status_code
                == 401
            )
        assert (
            client_fb.post(
                "/api/v1/auth/login",
                json={"username": TEST_OWNER, "password": "bad-password-123"},
            ).status_code
            == 429
        )

    # 3. DB connection outage -> 503 DATABASE_UNAVAILABLE; after recovery, revoked session is 401
    from sqlalchemy.exc import OperationalError

    from media_os_api.auth import create_redis_client

    rclient = create_redis_client(redis_url)
    for key in rclient.scan_iter("media_os:rl:login:*"):
        rclient.delete(key)
    rclient.close()

    with _make_app(monkeypatch, db_url=db_url, redis_url=redis_url) as client_db:
        login_r = client_db.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
            headers={"Origin": "http://localhost:3000"},
        )
        assert login_r.status_code == 200
        csrf_tok = login_r.json()["csrf_token"]
        revoked_cookie = client_db.cookies.get("media_os_session")
        assert revoked_cookie

        assert (
            client_db.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": csrf_tok, "Origin": "http://localhost:3000"},
            ).status_code
            == 200
        )

        # Create a second independent active session after logging out the first
        login_r2 = client_db.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASS_1},
            headers={"Origin": "http://localhost:3000"},
        )
        assert login_r2.status_code == 200
        active_cookie = client_db.cookies.get("media_os_session")
        assert active_cookie

    # Simulate transient DB outage in a restarted API process (empty in-memory revocation store)
    with _make_app(monkeypatch, db_url=db_url, redis_url=redis_url) as restarted_client:

        def _db_down(*_args: object, **_kwargs: object) -> object:
            raise OperationalError("SELECT 1", {}, Exception("db down"))

        with monkeypatch.context() as mctx:
            mctx.setattr("media_os_api.auth.dependencies.verify_persisted_session", _db_down)
            restarted_client.cookies.set("media_os_session", active_cookie)
            outage_active = restarted_client.get("/api/v1/auth/me")
            assert outage_active.status_code == 503
            assert outage_active.json()["error"]["code"] == "DATABASE_UNAVAILABLE"

            restarted_client.cookies.set("media_os_session", revoked_cookie)
            outage_revoked = restarted_client.get("/api/v1/auth/me")
            assert outage_revoked.status_code == 503
            assert outage_revoked.json()["error"]["code"] == "DATABASE_UNAVAILABLE"

        # Once DB recovers, active session works (200) and revoked session remains rejected (401)
        restarted_client.cookies.set("media_os_session", active_cookie)
        assert restarted_client.get("/api/v1/auth/me").status_code == 200

        restarted_client.cookies.set("media_os_session", revoked_cookie)
        recovered_resp = restarted_client.get("/api/v1/auth/me")
        assert recovered_resp.status_code == 401
        assert recovered_resp.json()["error"]["code"] == "SESSION_INVALID"
