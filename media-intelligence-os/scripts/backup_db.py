"""PostgreSQL custom-format backup utility for Media Intelligence OS (Phase 4).

Stores timestamped `.dump` and `.sha256` artifacts outside the Git repository.
Never prints database passwords or connection strings to stdout/stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT.parent / "media-os-runtime" / ".env"
DEFAULT_BACKUP_DIR = REPO_ROOT.parent / "media-os-backups"


def _load_pg_config(env_file: Path | None) -> tuple[str, str, str, str]:
    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip('"').strip("'")

    container = os.environ.get("MEDIA_OS_PG_CONTAINER", "media-os-postgres")
    pg_user = os.environ.get("POSTGRES_USER") or values.get("POSTGRES_USER") or "media_os_user"
    pg_db = os.environ.get("POSTGRES_DB") or values.get("POSTGRES_DB") or "media_os"
    pg_pass = os.environ.get("POSTGRES_PASSWORD") or values.get("POSTGRES_PASSWORD") or ""
    if not pg_pass:
        raise SystemExit("POSTGRES_PASSWORD is required in environment or external .env file.")
    return container, pg_user, pg_db, pg_pass


def create_backup(
    *,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> Path:
    """Dump the application PostgreSQL database to a timestamped `.dump` file outside Git."""
    resolved_dir = backup_dir.resolve()
    if REPO_ROOT in resolved_dir.parents or resolved_dir == REPO_ROOT:
        raise ValueError("Refusing to write database backups inside the tracked Git repository.")

    container, pg_user, pg_db, pg_pass = _load_pg_config(env_file)
    resolved_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dump_path = resolved_dir / f"media_os_{stamp}.dump"
    sha_path = resolved_dir / f"media_os_{stamp}.dump.sha256"

    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={pg_pass}",
            container,
            "pg_dump",
            "-U",
            pg_user,
            "-d",
            pg_db,
            "-Fc",
            "--no-owner",
            "--no-privileges",
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout:
        stderr_safe = (proc.stderr or b"").decode("utf-8", "replace").replace(pg_pass, "[REDACTED]")
        raise RuntimeError(f"pg_dump failed (exit={proc.returncode}): {stderr_safe}")

    dump_path.write_bytes(proc.stdout)

    # Verify archive TOC via pg_restore --list
    toc_proc = subprocess.run(
        ["docker", "exec", "-i", container, "pg_restore", "--list"],
        input=proc.stdout,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if toc_proc.returncode != 0 or not toc_proc.stdout:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError("Backup archive verification via `pg_restore --list` failed.")

    digest = hashlib.sha256(proc.stdout).hexdigest()
    sha_path.write_text(f"{digest}  {dump_path.name}\n", encoding="utf-8")
    return dump_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help="Destination backup directory outside Git.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="External runtime .env file holding PostgreSQL credentials.",
    )
    args = parser.parse_args()

    try:
        dump_path = create_backup(
            backup_dir=Path(args.output_dir),
            env_file=Path(args.env_file) if args.env_file else None,
        )
    except Exception as exc:
        print(f"[BACKUP-FAIL] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] PostgreSQL backup created and TOC-verified: {dump_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
