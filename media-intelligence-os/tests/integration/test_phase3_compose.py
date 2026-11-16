"""Integration tests validating application Docker Compose topology, security, and isolation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_COMPOSE_FILE = REPO_ROOT / "infra" / "docker" / "compose.app.yml"

pytestmark = pytest.mark.integration


def _render_app_compose_config(*, interpolate: bool = True) -> dict[str, Any]:
    cmd = ["docker", "compose", "-f", str(APP_COMPOSE_FILE), "config"]
    env = dict(os.environ)
    if interpolate:
        # Provide synthetic placeholders only for required compose fail-closed expressions;
        # real secrets must never be stored in the repository or required for static CI validation.
        env.setdefault("POSTGRES_PASSWORD", "test-placeholder-pg-only")
        env.setdefault("REDIS_PASSWORD", "test-placeholder-redis-only")
        # Clear optional secrets so resolved compose config reflects empty-default behavior.
        # Without this, host env leaks would make the static topology test non-deterministic.
        for _secret_key in ("SESSION_SECRET", "OWNER_PASSWORD_HASH", "OWNER_PASSWORD_HASH_B64"):
            env.pop(_secret_key, None)
    else:
        cmd.append("--no-interpolate")
    cmd.extend(["--format", "json"])
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"docker compose config failed:\n{proc.stderr}"
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


def test_compose_app_enforces_phase4_topology_loopback_and_memory_limits() -> None:
    raw_cfg = _render_app_compose_config(interpolate=False)
    cfg = _render_app_compose_config(interpolate=True)

    assert cfg["name"] == "media-intelligence-os"

    services = cfg["services"]
    assert sorted(services.keys()) == [
        "media-os-api",
        "media-os-creative",
        "media-os-postgres",
        "media-os-redis",
        "media-os-scheduler",
        "media-os-web",
        "media-os-worker",
    ]
    assert "postiz" not in services

    api = services["media-os-api"]
    web = services["media-os-web"]
    pg = services["media-os-postgres"]
    redis = services["media-os-redis"]
    worker = services["media-os-worker"]
    scheduler = services["media-os-scheduler"]
    creative = services["media-os-creative"]

    for svc in (api, web, worker, scheduler, creative):
        assert svc["read_only"] is True
        assert "no-new-privileges:true" in svc.get("security_opt", [])

    for svc in (pg, redis):
        assert "no-new-privileges:true" in svc.get("security_opt", [])
        # Database and Redis must not publish ports to Windows host by default
        assert not svc.get("ports")

    # Enforce calibrated memory caps under 16 GB Windows + WSL2 host budget
    assert int(api["mem_limit"]) <= 256 * 1024 * 1024
    assert int(web["mem_limit"]) <= 256 * 1024 * 1024
    assert int(pg["mem_limit"]) <= 512 * 1024 * 1024
    assert int(redis["mem_limit"]) <= 128 * 1024 * 1024
    assert int(worker["mem_limit"]) <= 256 * 1024 * 1024
    assert int(scheduler["mem_limit"]) <= 128 * 1024 * 1024
    assert int(creative["mem_limit"]) <= 768 * 1024 * 1024

    # N2 creative worker: creative queue only, no published ports, no AI or research tokens,
    # brand assets read-only; the API sees rendered media read-only.
    assert creative["command"][-3:] == ["creative", "-n", "creative@%h"]
    assert not creative.get("ports")
    secret_markers = ("GEMINI", "GROQ", "OPENROUTER", "CLOUDFLARE", "GITHUB_TOKEN")
    assert not [k for k in creative["environment"] if k.startswith(secret_markers)]
    mounts = {v["target"]: v.get("read_only", False) for v in creative["volumes"]}
    assert mounts == {"/brand-assets/brand-app-promo": True, "/data/media": False}
    # The API reads rendered media and is the only writer of owner uploads; the worker reads both.
    assert {v["target"]: v.get("read_only", False) for v in api["volumes"]} == {
        "/data/media": True,
        "/data/uploads": False,
    }
    assert {v["target"]: v.get("read_only", False) for v in worker["volumes"]} == {
        "/data/media": True,
        "/data/uploads": True,
    }
    assert "/data/uploads" not in mounts  # the renderer never sees uploads
    assert "creative" not in worker["command"][-1]

    # Strict loopback-only host bindings for web and api
    api_ports = api["ports"]
    web_ports = web["ports"]
    assert len(api_ports) == 1
    assert api_ports[0]["host_ip"] == "127.0.0.1"
    assert int(api_ports[0]["published"]) == 8000
    assert int(api_ports[0]["target"]) == 8000

    assert len(web_ports) == 1
    assert web_ports[0]["host_ip"] == "127.0.0.1"
    assert int(web_ports[0]["published"]) == 3100  # 3000 is used by another local project
    assert int(web_ports[0]["target"]) == 3000

    # Complete network and volume isolation from external Postiz and Phase 1 baseline stacks
    networks = cfg["networks"]
    assert list(networks.keys()) == ["media-os-net"]
    assert "postiz-network" not in networks
    assert "temporal-network" not in networks
    assert "media-os-baseline-net" not in networks

    volumes = cfg.get("volumes", {})
    assert sorted(volumes.keys()) == [
        "media_os_media",
        "media_os_pgdata",
        "media_os_redisdata",
        "media_os_uploads",
    ]

    # Web must communicate internally over docker network without hardcoded secrets
    web_env = web["environment"]
    assert web_env["INTERNAL_API_BASE_URL"] == "http://media-os-api:8000"
    assert web_env["NODE_ENV"] == "production"
    assert not any(k.startswith("NEXT_PUBLIC_") for k in web_env)

    # DB and Redis require explicit secret provisioning; API/worker contain zero hardcoded literals
    raw_pg_env = raw_cfg["services"]["media-os-postgres"]["environment"]
    raw_redis_env = raw_cfg["services"]["media-os-redis"]["environment"]
    raw_api_env = raw_cfg["services"]["media-os-api"]["environment"]
    assert raw_pg_env["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
    assert raw_redis_env["REDIS_PASSWORD"] == "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
    assert raw_api_env.get("OWNER_PASSWORD_HASH") == "${OWNER_PASSWORD_HASH:-}"
    assert raw_api_env.get("SESSION_SECRET") == "${SESSION_SECRET:-}"
    assert api["environment"].get("OWNER_PASSWORD_HASH", "") == ""
    assert api["environment"].get("SESSION_SECRET", "") == ""
