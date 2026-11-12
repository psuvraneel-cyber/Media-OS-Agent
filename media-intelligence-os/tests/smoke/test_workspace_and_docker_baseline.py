"""End-to-end smoke tests for Phase 1 workspace layout, security hygiene, and Docker baseline."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

import pytest

from scripts.generate_ref_manifests import verify_manifests
from scripts.scan_secrets import scan_repo

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker" / "compose.baseline.yml"
CONTAINER_NAME = "media-os-baseline-api"
HEALTH_URL = "http://127.0.0.1:8000/health"

pytestmark = pytest.mark.smoke


def _run_cmd(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _list_non_baseline_containers() -> list[str]:
    proc = _run_cmd(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=30)
    assert proc.returncode == 0, proc.stderr
    return sorted(
        name.strip()
        for name in proc.stdout.splitlines()
        if name.strip() and name.strip() != CONTAINER_NAME
    )


def _assert_live_health() -> None:
    inspect_proc = _run_cmd(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", CONTAINER_NAME],
        timeout=30,
    )
    assert inspect_proc.returncode == 0, inspect_proc.stderr
    assert inspect_proc.stdout.strip() == "healthy"

    with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["status"] == "ok"
        assert body["service"] == "media-os-baseline-api"


def test_workspace_structure_and_docs_exist() -> None:
    required_paths = [
        ".gitignore",
        ".env.example",
        "README.md",
        "CONTRIBUTING.md",
        "pyproject.toml",
        ".pre-commit-config.yaml",
        ".github/workflows/ci.yml",
        "apps/web",
        "apps/api",
        "workers/research",
        "workers/creative",
        "services/mcp",
        "infra/docker/Dockerfile.baseline",
        "infra/docker/compose.baseline.yml",
        "docs/PRD.md",
        "docs/ARCHITECTURE.md",
        "docs/DECISIONS.md",
        "docs/SECURITY.md",
        "docs/OPERATIONS.md",
        "docs/ENVIRONMENT.md",
        "docs/REQUIREMENTS_TRACEABILITY.md",
        "references/README.md",
        "scripts/scan_secrets.py",
        "scripts/generate_ref_manifests.py",
        "tests/unit",
        "tests/integration",
        "tests/fixtures",
        "tests/smoke",
    ]
    for rel in required_paths:
        assert (REPO_ROOT / rel).exists(), f"Missing required path: {rel}"

    assert scan_repo(REPO_ROOT) == []


def test_reference_manifests_if_present_locally() -> None:
    parent_postiz = REPO_ROOT.parent / "postiz-app-main"
    parent_socialai = REPO_ROOT.parent / "socialai-agent-main"
    if not (parent_postiz.is_dir() and parent_socialai.is_dir()):
        pytest.skip("Parent reference snapshots are not co-located on CI runner.")
    assert verify_manifests() == 0


def test_docker_baseline_build_start_health_stop_and_restart() -> None:
    before_external = _list_non_baseline_containers()

    # Guarantee baseline container is not left stale before starting
    _run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], timeout=60)

    try:
        cfg = _run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "config"], timeout=30)
        assert cfg.returncode == 0, cfg.stderr

        build = _run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "build"], timeout=300)
        assert build.returncode == 0, build.stderr

        up1 = _run_cmd(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
            timeout=120,
        )
        assert up1.returncode == 0, up1.stderr
        _assert_live_health()

        down1 = _run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], timeout=60)
        assert down1.returncode == 0, down1.stderr

        # Restart again to prove no unexpected leftover state or port conflicts
        up2 = _run_cmd(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"],
            timeout=120,
        )
        assert up2.returncode == 0, up2.stderr
        _assert_live_health()
    finally:
        _run_cmd(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], timeout=60)

    after_external = _list_non_baseline_containers()
    assert after_external == before_external
