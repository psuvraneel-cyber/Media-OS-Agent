"""Integration tests for PostgreSQL backup, isolated restore, and live container restarts."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from scripts.backup_db import create_backup
from scripts.restore_verify import verify_restore_in_temp_db

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_ENV = Path(r"C:\Media OS Agent\media-os-runtime\.env")


def _docker_stack_running() -> bool:
    proc = subprocess.run(
        ["docker", "ps", "--filter", "name=media-os-postgres", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return "media-os-postgres" in proc.stdout


def test_backup_and_isolated_restore_verification() -> None:
    if not (_docker_stack_running() and EXTERNAL_ENV.is_file()):
        pytest.skip("Live media-os-postgres container or external .env not present (CI/offline).")

    with tempfile.TemporaryDirectory(prefix="media_os_backup_test_") as tmpdir:
        dump_path = create_backup(backup_dir=Path(tmpdir), env_file=EXTERNAL_ENV)
        assert dump_path.is_file()
        assert dump_path.stat().st_size > 0
        assert dump_path.with_suffix(".dump.sha256").is_file()

        result = verify_restore_in_temp_db(dump_path=dump_path, env_file=EXTERNAL_ENV)
        assert result.source_schema_version == "0003_domain_integrity_hardening"
        assert result.restored_schema_version == "0003_domain_integrity_hardening"
        assert result.brand_checksum_match is True
        assert result.schedule_checksum_match is True
        assert result.job_checksum_match is True
        assert result.owner_checksum_match is True
        assert result.row_counts["brand_profiles"] == 2
        assert result.row_counts["owner_users"] >= 1


def test_live_container_readiness_and_postiz_isolation() -> None:
    if os.environ.get("RUN_LIVE_RESTART_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_RESTART_TESTS=1 to run live container restart tests.")

    assert httpx.get("http://127.0.0.1:8000/health/ready", timeout=5).status_code == 200
    assert httpx.get("http://127.0.0.1:3000/api/health", timeout=5).status_code == 200
    assert httpx.get("http://localhost:4007/api/auth/can-register", timeout=5).status_code == 200

    # Restart each service cleanly and confirm health recovers without touching Postiz
    for container in (
        "media-os-postgres",
        "media-os-redis",
        "media-os-worker",
        "media-os-scheduler",
        "media-os-api",
        "media-os-web",
    ):
        proc = subprocess.run(
            ["docker", "restart", container],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

        # Wait up to 45s for container health
        for _ in range(45):
            insp = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if insp.stdout.strip() == "healthy":
                break
            time.sleep(1)
        else:
            pytest.fail(f"Container {container} did not recover healthy after restart.")

    assert httpx.get("http://127.0.0.1:8000/health/ready", timeout=5).status_code == 200
    assert httpx.get("http://127.0.0.1:3000/api/health", timeout=5).status_code == 200
    assert httpx.get("http://localhost:4007/api/auth/can-register", timeout=5).status_code == 200
