"""Integration tests validating Docker Compose baseline topology and boundaries."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker" / "compose.baseline.yml"

pytestmark = pytest.mark.integration


def _render_compose_config() -> dict[str, Any]:
    proc = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"docker compose config failed:\n{proc.stderr}"
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


def test_compose_baseline_is_minimal_isolated_and_loopback_bound() -> None:
    cfg = _render_compose_config()

    assert cfg["name"] == "media-os-baseline"

    services = cfg["services"]
    assert list(services.keys()) == ["baseline-api"]

    api = services["baseline-api"]
    assert api["container_name"] == "media-os-baseline-api"
    assert api["read_only"] is True

    ports = api["ports"]
    assert len(ports) == 1
    assert ports[0]["host_ip"] == "127.0.0.1"
    assert int(ports[0]["published"]) == 8000
    assert int(ports[0]["target"]) == 8000

    networks = cfg["networks"]
    assert list(networks.keys()) == ["media-os-baseline-net"]
    assert "postiz-network" not in networks
    assert "temporal-network" not in networks
    assert not cfg.get("volumes")
