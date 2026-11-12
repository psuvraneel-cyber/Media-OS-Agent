"""Integration tests exercising the Phase 3 FastAPI backend, auth, CSRF, schemas, and logs."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from media_os_api.auth import hash_password
from media_os_api.config import load_settings
from media_os_api.main import create_app
from media_os_api.schemas.system import PostizReachabilitySummary

pytestmark = pytest.mark.integration

TEST_OWNER = "local-owner"
TEST_PASSWORD = "Phase3-Strong-Test-Password!2026"
TEST_SESSION_SECRET = "phase3-test-session-secret-0123456789abcdef0123456789abcdef"


def _build_test_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _fake_probe(
        base_url: str, *, timeout_seconds: float = 2.0
    ) -> PostizReachabilitySummary:
        return PostizReachabilitySummary(
            status="reachable",
            checked_at_utc="2026-11-10T00:00:00+00:00",
            detail="Mocked Postiz HTTP reachability probe for offline CI tests.",
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
            "OWNER_PASSWORD_HASH": hash_password(TEST_PASSWORD),
            "SESSION_SECRET": TEST_SESSION_SECRET,
            "COOKIE_SECURE": "false",
            "CORS_ALLOWED_ORIGINS": "http://127.0.0.1:3000,http://localhost:3000",
            "LOGIN_RATE_LIMIT_MAX_ATTEMPTS": "3",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS": "120",
            "POSTIZ_BASE_URL": "http://127.0.0.1:4007",
        }
    )
    app = create_app(settings)
    return TestClient(app)


def test_health_live_and_ready_honest_reporting(monkeypatch: pytest.MonkeyPatch) -> None:
    with _build_test_client(monkeypatch) as client:
        live = client.get("/health/live", headers={"X-Request-ID": "req-live-0001"})
        assert live.status_code == 200
        assert live.headers["X-Request-ID"] == "req-live-0001"
        assert live.headers["X-Content-Type-Options"] == "nosniff"
        assert live.headers["X-Frame-Options"] == "DENY"
        assert live.json()["status"] == "ok"

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["persistent_storage"] is False
        checks = {item["name"]: item for item in body["checks"]}
        assert checks["configuration"]["status"] == "ready"
        assert checks["auth_bootstrap"]["status"] == "ready"
        assert checks["database"]["status"] == "not_implemented"
        assert checks["redis"]["status"] == "not_implemented"


def test_protected_routes_reject_unauthenticated_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_test_client(monkeypatch) as client:
        for path in (
            "/api/v1/auth/me",
            "/api/v1/system/status",
            "/api/v1/system/logs",
            "/api/v1/brands",
            "/api/v1/channels",
        ):
            resp = client.get(path)
            assert resp.status_code == 401
            payload = resp.json()
            assert payload["error"]["code"] == "UNAUTHORIZED"
            assert payload["error"]["request_id"]


def test_full_auth_csrf_revocation_rate_limit_and_boundary_apis(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    with _build_test_client(monkeypatch) as client:
        # Cross-origin login attempt must fail closed with 403
        evil_origin = client.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASSWORD},
            headers={"Origin": "https://evil.example"},
        )
        assert evil_origin.status_code == 403
        assert evil_origin.json()["error"]["code"] == "ORIGIN_FORBIDDEN"

        # Repeated invalid attempts trigger 429 rate limiting
        for _ in range(3):
            bad = client.post(
                "/api/v1/auth/login",
                json={"username": TEST_OWNER, "password": "wrong-password-123"},
            )
            assert bad.status_code == 401
            assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"

        limited = client.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": "wrong-password-123"},
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert int(limited.headers["Retry-After"]) >= 1

        # Separate app instance for clean login + session lifecycle verification
    with _build_test_client(monkeypatch) as client:
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": TEST_OWNER, "password": TEST_PASSWORD},
            headers={"Origin": "http://localhost:3000"},
        )
        assert login_resp.status_code == 200
        assert login_resp.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        login_data = login_resp.json()
        assert login_data["authenticated"] is True
        assert login_data["username"] == TEST_OWNER
        csrf_token = login_data["csrf_token"]
        session_cookie = client.cookies.get("media_os_session")
        assert session_cookie
        assert client.cookies.get("media_os_csrf") == csrf_token
        set_cookies = login_resp.headers.get_list("set-cookie")
        session_header = next(h for h in set_cookies if h.startswith("media_os_session="))
        assert "HttpOnly" in session_header
        assert "samesite=lax" in session_header.lower()

        # Authenticated reads work cleanly
        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["username"] == TEST_OWNER

        status_resp = client.get("/api/v1/system/status")
        assert status_resp.status_code == 200
        sys_data: dict[str, Any] = status_resp.json()
        assert sys_data["persistent_storage"] is False
        assert sys_data["csrf_protection"] is True
        assert sys_data["postiz"]["status"] == "reachable"
        modules = {m["module_id"]: m for m in sys_data["modules"]}
        assert modules["reddit-source-policy"]["status"] == "blocked_by_policy"
        assert modules["postgres-redis-celery"]["status"] == "not_implemented"

        brands_resp = client.get("/api/v1/brands")
        assert brands_resp.status_code == 200
        brands_json = brands_resp.json()
        assert brands_json["is_prototype_fixture"] is True
        assert brands_json["persistent"] is False
        brand_map = {item["brand_id"]: item for item in brands_json["items"]}
        assert set(brand_map.keys()) == {"brand-tech-ai", "brand-app-promo"}
        assert brand_map["brand-app-promo"]["placeholder_unconfigured"] is True

        tech_channels = client.get("/api/v1/channels", params={"brand_id": "brand-tech-ai"})
        assert tech_channels.status_code == 200
        assert all(c["brand_id"] == "brand-tech-ai" for c in tech_channels.json()["items"])
        assert all(c["publishing_enabled"] is False for c in tech_channels.json()["items"])

        missing_brand = client.get("/api/v1/brands/nonexistent-brand")
        assert missing_brand.status_code == 404
        assert missing_brand.json()["error"]["code"] == "BRAND_NOT_FOUND"

        logs_resp = client.get("/api/v1/system/logs")
        assert logs_resp.status_code == 200
        logs_json = logs_resp.json()
        assert logs_json["ephemeral"] is True
        assert logs_json["persistent"] is False
        assert len(logs_json["items"]) >= 3

        # Logout CSRF enforcement
        no_csrf = client.post("/api/v1/auth/logout")
        assert no_csrf.status_code == 403
        assert no_csrf.json()["error"]["code"] == "CSRF_REQUIRED"

        bad_csrf = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "forged-csrf-token"},
        )
        assert bad_csrf.status_code == 403
        assert bad_csrf.json()["error"]["code"] == "CSRF_INVALID"

        good_logout = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
        )
        assert good_logout.status_code == 200
        assert good_logout.json()["authenticated"] is False

        # Replaying old session cookie after logout must fail due to server-side revocation
        client.cookies.set("media_os_session", session_cookie)
        replay = client.get("/api/v1/auth/me")
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "SESSION_INVALID"

    # Confirm credentials never appeared in captured logs
    joined_logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert TEST_PASSWORD not in joined_logs
    assert TEST_SESSION_SECRET not in joined_logs


def test_validation_error_uses_structured_envelope_without_secret_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_test_client(monkeypatch) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": "MySecretNotToEcho999!"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["request_id"]
        assert "MySecretNotToEcho999!" not in str(body)

        openapi_resp = client.get("/openapi.json")
        assert openapi_resp.status_code == 200
        spec = openapi_resp.json()
        assert "/health/live" in spec["paths"]
        assert "/health/ready" in spec["paths"]
        assert "/api/v1/auth/login" in spec["paths"]
        assert "/api/v1/system/status" in spec["paths"]
