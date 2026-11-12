"""Integration tests verifying standalone Postiz v2.25.3 API, MCP, and security gates.

These tests read credentials locally from `C:\\Media OS Agent\\postiz-runtime\\.api.env`
(or `POSTIZ_BASE_URL` / `POSTIZ_API_KEY` env vars) without printing secrets.
When run in CI without a local `postiz-runtime` instance, tests skip cleanly.
No test creates fake social integrations or publishes any content.
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request
from typing import Any

import pytest

DEFAULT_API_ENV = pathlib.Path(r"C:\Media OS Agent\postiz-runtime\.api.env")

pytestmark = pytest.mark.integration


def _load_postiz_credentials() -> tuple[str, str]:
    base_url = os.environ.get("POSTIZ_BASE_URL", "").strip()
    api_key = os.environ.get("POSTIZ_API_KEY", "").strip()

    if (not base_url or not api_key) and DEFAULT_API_ENV.is_file():
        for raw_line in DEFAULT_API_ENV.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            cleaned = val.strip().strip('"').strip("'")
            if key.strip() == "POSTIZ_BASE_URL" and not base_url:
                base_url = cleaned
            elif key.strip() == "POSTIZ_API_KEY" and not api_key:
                api_key = cleaned

    if not base_url or not api_key or "REPLACE_WITH_" in api_key:
        pytest.skip("Local postiz-runtime .api.env credentials not present (CI/offline).")

    return base_url.rstrip("/"), api_key


def _http_request(
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    secret_to_redact: str = "",
) -> tuple[int, dict[str, str], str]:
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"Unsupported URL scheme in base_url: {base_url}")
    url = f"{base_url}{path}"
    req_headers = dict(headers or {})
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    if data is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)  # noqa: S310

    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: urllib.request.Request,
            fp: Any,
            code: int,
            msg: str,
            headers: Any,
            newurl: str,
        ) -> urllib.request.Request | None:
            return None

    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if secret_to_redact:
                raw = raw.replace(secret_to_redact, "<REDACTED>")
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        if secret_to_redact:
            raw = raw.replace(secret_to_redact, "<REDACTED>")
        return exc.code, dict(exc.headers), raw
    except urllib.error.URLError as exc:
        pytest.skip(f"Postiz endpoint unreachable at {base_url}: {exc.reason}")


def _parse_mcp_payload(content_type: str | None, body: str) -> list[dict[str, Any]]:
    if content_type and "text/event-stream" in content_type.lower():
        events: list[dict[str, Any]] = []
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    events.append(json.loads(payload))
        return events
    parsed = json.loads(body)
    return parsed if isinstance(parsed, list) else [parsed]


def test_postiz_public_api_auth_and_rejections() -> None:
    base_url, api_key = _load_postiz_credentials()

    status_missing, _, body_missing = _http_request(base_url, "GET", "/api/public/v1/me")
    assert status_missing == 401
    assert "No API Key" in body_missing

    status_bad, _, body_bad = _http_request(
        base_url,
        "GET",
        "/api/public/v1/me",
        headers={"Authorization": "invalid-test-api-key"},
    )
    assert status_bad == 401
    assert "Invalid API key" in body_bad

    # Public REST API expects raw Authorization: <key> rather than Bearer prefix.
    status_bearer, _, _ = _http_request(
        base_url,
        "GET",
        "/api/public/v1/me",
        headers={"Authorization": f"Bearer {api_key}"},
        secret_to_redact=api_key,
    )
    assert status_bearer == 401

    status_ok, _, body_ok = _http_request(
        base_url,
        "GET",
        "/api/public/v1/me",
        headers={"Authorization": api_key},
        secret_to_redact=api_key,
    )
    assert status_ok == 200
    me_data = json.loads(body_ok)
    assert me_data["organization"]["id"]
    assert me_data["organization"]["name"]

    status_conn, _, body_conn = _http_request(
        base_url,
        "GET",
        "/api/public/v1/is-connected",
        headers={"Authorization": api_key},
        secret_to_redact=api_key,
    )
    assert status_conn == 200
    assert json.loads(body_conn) == {"connected": True}


def test_postiz_public_api_integrations_and_empty_state() -> None:
    base_url, api_key = _load_postiz_credentials()
    auth = {"Authorization": api_key}

    st_int, _, body_int = _http_request(
        base_url, "GET", "/api/public/v1/integrations", headers=auth, secret_to_redact=api_key
    )
    assert st_int == 200
    integrations = json.loads(body_int)
    assert isinstance(integrations, list)

    st_grp, _, body_grp = _http_request(
        base_url, "GET", "/api/public/v1/groups", headers=auth, secret_to_redact=api_key
    )
    assert st_grp == 200
    assert isinstance(json.loads(body_grp), list)

    st_posts, _, body_posts = _http_request(
        base_url,
        "GET",
        "/api/public/v1/posts?startDate=2026-11-01T00:00:00.000Z&endDate=2026-11-30T23:59:59.000Z",
        headers=auth,
        secret_to_redact=api_key,
    )
    assert st_posts == 200
    posts_payload = json.loads(body_posts)
    assert isinstance(posts_payload.get("posts"), list)


def test_postiz_registration_disabled_while_login_active() -> None:
    base_url, _ = _load_postiz_credentials()

    st_can, _, body_can = _http_request(base_url, "GET", "/api/auth/can-register")
    assert st_can == 200
    assert json.loads(body_can) == {"register": False}

    st_reg, _, body_reg = _http_request(
        base_url,
        "POST",
        "/api/auth/register",
        json_body={
            "email": "blocked-registration-check@example.invalid",
            "password": "NotARealPassword123!",
            "company": "BlockedTestOrg",
            "provider": "LOCAL",
            "providerToken": "",
        },
    )
    assert st_reg == 400
    assert "Registration is disabled" in body_reg

    st_login, _, body_login = _http_request(
        base_url,
        "POST",
        "/api/auth/login",
        json_body={
            "email": "nonexistent-login-probe@example.invalid",
            "password": "NotARealPassword123!",
            "provider": "LOCAL",
            "providerToken": "",
        },
    )
    assert st_login == 400
    assert "Invalid user name or password" in body_login


def test_postiz_mcp_routing_auth_and_tool_discovery() -> None:
    base_url, api_key = _load_postiz_credentials()
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "media-os-integration-test", "version": "0.1.0"},
        },
    }
    accept_hdr = "application/json, text/event-stream"

    # Root `/mcp` hits Next.js frontend and redirects rather than hitting backend MCP.
    st_root, _, _ = _http_request(
        base_url,
        "POST",
        "/mcp",
        headers={"Authorization": f"Bearer {api_key}", "Accept": accept_hdr},
        json_body=init_body,
        secret_to_redact=api_key,
    )
    assert st_root in {307, 404}

    st_no_auth, _, _ = _http_request(
        base_url,
        "POST",
        "/api/mcp",
        headers={"Accept": accept_hdr},
        json_body=init_body,
    )
    assert st_no_auth == 401

    st_bad_auth, _, _ = _http_request(
        base_url,
        "POST",
        "/api/mcp",
        headers={"Authorization": "Bearer invalid-mcp-token", "Accept": accept_hdr},
        json_body=init_body,
    )
    assert st_bad_auth == 401

    auth_headers = {"Authorization": f"Bearer {api_key}", "Accept": accept_hdr}
    st_init, hdr_init, body_init = _http_request(
        base_url,
        "POST",
        "/api/mcp",
        headers=auth_headers,
        json_body=init_body,
        secret_to_redact=api_key,
    )
    assert st_init == 200
    ct_init = hdr_init.get("Content-Type") or hdr_init.get("content-type")
    init_events = _parse_mcp_payload(ct_init, body_init)
    assert init_events[0]["result"]["serverInfo"]["name"] == "Postiz MCP"

    st_notif, _, _ = _http_request(
        base_url,
        "POST",
        "/api/mcp",
        headers=auth_headers,
        json_body={"jsonrpc": "2.0", "method": "notifications/initialized"},
        secret_to_redact=api_key,
    )
    assert st_notif in {200, 202}

    st_tools, hdr_tools, body_tools = _http_request(
        base_url,
        "POST",
        "/api/mcp",
        headers=auth_headers,
        json_body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        secret_to_redact=api_key,
    )
    assert st_tools == 200
    ct_tools = hdr_tools.get("Content-Type") or hdr_tools.get("content-type")
    tool_events = _parse_mcp_payload(ct_tools, body_tools)
    tools = tool_events[0]["result"]["tools"]
    assert len(tools) > 0
    tool_names = {tool["name"] for tool in tools}
    assert {"integrationList", "postsListTool", "integrationSchema"}.issubset(tool_names)
    for tool in tools:
        schema = tool.get("inputSchema", {})
        assert schema.get("type") == "object"
        assert isinstance(schema.get("properties"), dict)


def test_postiz_draft_creation_requires_connected_integration() -> None:
    base_url, api_key = _load_postiz_credentials()
    auth = {"Authorization": api_key}

    st_int, _, body_int = _http_request(
        base_url, "GET", "/api/public/v1/integrations", headers=auth, secret_to_redact=api_key
    )
    assert st_int == 200
    integrations = json.loads(body_int)
    if integrations:
        pytest.skip("Connected integrations exist; Phase 2 un-integrated draft check skipped.")

    st_draft, _, body_draft = _http_request(
        base_url,
        "POST",
        "/api/public/v1/posts",
        headers=auth,
        json_body={
            "type": "draft",
            "date": "2026-11-15T10:00:00.000Z",
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": ""},
                    "value": [{"content": "PHASE 2 TEST DRAFT DO NOT PUBLISH", "image": []}],
                    "settings": {},
                }
            ],
        },
        secret_to_redact=api_key,
    )
    assert st_draft == 400
    assert "All posts must have an integration id" in body_draft
