"""Run the integration suite against a disposable PostgreSQL/Redis stack, never the runtime one.

Steps: start `infra/docker/compose.test.yml` (project `media-os-test`, tmpfs storage) → run Alembic
to head → run pytest with test URLs → stop the stack (data is discarded with the tmpfs).

Postiz probe tests are excluded unless `--include-postiz` is passed, so the separate Postiz
deployment is never contacted by default.

Usage:
    python scripts/run_integration_tests.py [--keep] [--include-postiz] [-- <extra pytest args>]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker" / "compose.test.yml"
PROJECT = "media-os-test"

# Throwaway values defined in compose.test.yml; not secrets.
TEST_DB_URL = (
    "postgresql+psycopg://media_os_it:it_only_disposable_password@127.0.0.1:55432/media_os_it_test"
)
TEST_REDIS_URL = "redis://127.0.0.1:56379/15"


def _compose(*args: str) -> list[str]:
    return ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args]


def _run(cmd: list[str], env: dict[str, str] | None = None) -> int:
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Leave the test stack running.")
    parser.add_argument("--include-postiz", action="store_true", help="Also run Postiz probes.")
    parser.add_argument("pytest_args", nargs="*", help="Extra args passed to pytest (after --).")
    args = parser.parse_args()

    if _run(_compose("up", "-d", "--wait")) != 0:
        print("[IT-FAIL] could not start disposable test stack", file=sys.stderr)
        return 1

    env = dict(os.environ)
    # Remove any runtime connection settings inherited from the shell.
    for key in ("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        env.pop(key, None)
    env["DATABASE_URL"] = TEST_DB_URL
    env["MEDIA_OS_TEST_DATABASE_URL"] = TEST_DB_URL
    env["MEDIA_OS_TEST_REDIS_URL"] = TEST_REDIS_URL

    try:
        rc = _run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
        if rc != 0:
            print("[IT-FAIL] alembic upgrade failed on disposable DB", file=sys.stderr)
            return rc
        pytest_cmd = [sys.executable, "-m", "pytest", "tests/integration", "-p", "no:cacheprovider"]
        if not args.include_postiz:
            pytest_cmd += ["--ignore", "tests/integration/test_postiz_api_mcp.py"]
        pytest_cmd += args.pytest_args
        return _run(pytest_cmd, env=env)
    finally:
        if not args.keep:
            # Plain `down`: the stack has no named volumes; tmpfs data disappears with it.
            _run(_compose("down"))


if __name__ == "__main__":
    raise SystemExit(main())
