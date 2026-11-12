"""Unit tests for the Phase 1 baseline service, `.env.example`, and `.gitignore`."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from media_os_api.health import BaselineConfig, build_health_payload, create_server
from scripts.scan_secrets import scan_repo

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit


def test_build_health_payload_fields() -> None:
    cfg = BaselineConfig(host="127.0.0.1", port=8000, app_env="test")
    payload = build_health_payload(cfg)
    assert payload["status"] == "ok"
    assert payload["service"] == "media-os-baseline-api"
    assert payload["phase"] == "phase-1-baseline"
    assert payload["environment"] == "test"
    assert isinstance(payload["timestamp_utc"], str)


def test_baseline_config_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "70000")
    with pytest.raises(ValueError, match="between 1 and 65535"):
        BaselineConfig.from_env()


def test_health_server_serves_ok_and_404() -> None:
    cfg = BaselineConfig(host="127.0.0.1", port=0, app_env="unit-test")
    server = create_server(cfg)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{actual_port}/health", timeout=3) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"
            assert data["environment"] == "unit-test"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{actual_port}/missing", timeout=3)
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_env_example_and_secret_scan_clean() -> None:
    env_example = REPO_ROOT / ".env.example"
    assert env_example.is_file()
    content = env_example.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=REPLACE_WITH_GEMINI_API_KEY" in content
    assert "SOURCE_REDDIT_STATUS=BLOCKED" in content
    assert scan_repo(REPO_ROOT) == []


def test_gitignore_blocks_secrets_without_blocking_source_code() -> None:
    ignored_paths = [
        ".env",
        ".env.local",
        "secrets/key.txt",
        "config/youtube_token.json",
        "config/client_credentials.json",
        "config/google_oauth.json",
        "data/brain.db",
    ]
    for rel_path in ignored_paths:
        res = subprocess.run(
            ["git", "check-ignore", "-q", rel_path],
            cwd=REPO_ROOT,
            check=False,
        )
        assert res.returncode == 0, f"Expected {rel_path} to be gitignored"

    allowed_source_paths = [
        ".env.example",
        "apps/api/src/media_os_api/token_budget.py",
        "tests/unit/test_token_accounting.py",
    ]
    for rel_path in allowed_source_paths:
        res = subprocess.run(
            ["git", "check-ignore", "-q", rel_path],
            cwd=REPO_ROOT,
            check=False,
        )
        assert res.returncode != 0, f"Legitimate source path {rel_path} must NOT be gitignored"
