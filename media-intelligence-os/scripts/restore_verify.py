"""Isolated PostgreSQL backup restoration and integrity verification script (Phase 4).

Restores a `.dump` archive into a separate temporary database inside `media-os-postgres`,
never touching or overwriting the live `media_os` database or Docker volumes.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.backup_db import DEFAULT_BACKUP_DIR, DEFAULT_ENV_FILE, _load_pg_config
except ModuleNotFoundError:  # pragma: no cover - direct CLI invocation (`python scripts/...`)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.backup_db import DEFAULT_BACKUP_DIR, DEFAULT_ENV_FILE, _load_pg_config

KEY_TABLES: tuple[str, ...] = (
    "owner_users",
    "auth_sessions",
    "brand_profiles",
    "channel_accounts",
    "schedule_definitions",
    "job_runs",
    "outbox_events",
    "audit_events",
    "content_packages",
    "brand_topic_assignments",
)


@dataclass(frozen=True)
class RestoreVerificationResult:
    """Verification report comparing live DB vs isolated restored temp DB."""

    dump_path: str
    temp_db_name: str
    source_schema_version: str
    restored_schema_version: str
    row_counts: dict[str, int]
    brand_checksum_match: bool
    schedule_checksum_match: bool
    job_checksum_match: bool
    owner_checksum_match: bool


def _exec_psql(
    *,
    container: str,
    pg_user: str,
    pg_db: str,
    pg_pass: str,
    sql: str,
) -> str:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={pg_pass}",
            container,
            "psql",
            "-U",
            pg_user,
            "-d",
            pg_db,
            "-t",
            "-A",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        safe_err = (proc.stderr or "").replace(pg_pass, "[REDACTED]")
        raise RuntimeError(f"psql failed on {pg_db}: {safe_err}")
    return proc.stdout.strip()


def _snapshot_metrics(
    *,
    container: str,
    pg_user: str,
    pg_db: str,
    pg_pass: str,
) -> tuple[str, dict[str, int], str, str, str, str]:
    schema_version = _exec_psql(
        container=container,
        pg_user=pg_user,
        pg_db=pg_db,
        pg_pass=pg_pass,
        sql="SELECT version_num FROM alembic_version LIMIT 1;",
    )
    counts: dict[str, int] = {}
    for tbl in KEY_TABLES:
        if tbl not in KEY_TABLES:  # pragma: no cover - defensive allowlist check
            raise ValueError(f"Unexpected table name: {tbl}")
        raw_count = _exec_psql(
            container=container,
            pg_user=pg_user,
            pg_db=pg_db,
            pg_pass=pg_pass,
            sql=f"SELECT COUNT(*) FROM {tbl};",  # noqa: S608
        )
        counts[tbl] = int(raw_count or "0")

    brand_digest = hashlib.sha256(
        _exec_psql(
            container=container,
            pg_user=pg_user,
            pg_db=pg_db,
            pg_pass=pg_pass,
            sql=(
                "SELECT COALESCE(string_agg(id::text || '|' || slug || '|' || name "
                "|| '|' || is_active::text, E'\\n' ORDER BY slug), '') FROM brand_profiles;"
            ),
        ).encode("utf-8")
    ).hexdigest()

    sched_digest = hashlib.sha256(
        _exec_psql(
            container=container,
            pg_user=pg_user,
            pg_db=pg_db,
            pg_pass=pg_pass,
            sql=(
                "SELECT COALESCE(string_agg(id::text || '|' || name || '|' || task_name "
                "|| '|' || is_enabled::text, E'\\n' ORDER BY id), '') "
                "FROM schedule_definitions;"
            ),
        ).encode("utf-8")
    ).hexdigest()

    job_digest = hashlib.sha256(
        _exec_psql(
            container=container,
            pg_user=pg_user,
            pg_db=pg_db,
            pg_pass=pg_pass,
            sql=(
                "SELECT COALESCE(string_agg(id::text || '|' || idempotency_key || '|' || status, "
                "E'\\n' ORDER BY id), '') FROM job_runs;"
            ),
        ).encode("utf-8")
    ).hexdigest()

    owner_digest = hashlib.sha256(
        _exec_psql(
            container=container,
            pg_user=pg_user,
            pg_db=pg_db,
            pg_pass=pg_pass,
            sql=(
                "SELECT COALESCE(string_agg(id::text || '|' || username || '|' || is_active::text "
                "|| '|' || token_version::text, E'\\n' ORDER BY id), '') FROM owner_users;"
            ),
        ).encode("utf-8")
    ).hexdigest()

    return schema_version, counts, brand_digest, sched_digest, job_digest, owner_digest


def verify_restore_in_temp_db(
    *,
    dump_path: Path,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> RestoreVerificationResult:
    """Restore `dump_path` into an isolated temporary DB, verify integrity, and clean up."""
    if not dump_path.is_file():
        raise FileNotFoundError(f"Backup dump not found: {dump_path}")

    sha_file = dump_path.with_suffix(".dump.sha256")
    dump_bytes = dump_path.read_bytes()
    if sha_file.is_file():
        expected_sha = sha_file.read_text(encoding="utf-8").strip().split()[0]
        actual_sha = hashlib.sha256(dump_bytes).hexdigest()
        if expected_sha != actual_sha:
            raise RuntimeError("Backup SHA-256 checksum mismatch; dump file may be corrupted.")

    container, pg_user, pg_db, pg_pass = _load_pg_config(env_file)
    temp_db = (
        f"media_os_restore_verify_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_"
        f"{secrets.token_hex(3)}"
    )

    src_ver, src_counts, src_brand_sha, src_sched_sha, src_job_sha, src_owner_sha = (
        _snapshot_metrics(
            container=container,
            pg_user=pg_user,
            pg_db=pg_db,
            pg_pass=pg_pass,
        )
    )

    _exec_psql(
        container=container,
        pg_user=pg_user,
        pg_db="postgres",
        pg_pass=pg_pass,
        sql=f'CREATE DATABASE "{temp_db}" OWNER "{pg_user}";',
    )
    try:
        restore_proc = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                f"PGPASSWORD={pg_pass}",
                container,
                "pg_restore",
                "-U",
                pg_user,
                "-d",
                temp_db,
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
            ],
            input=dump_bytes,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if restore_proc.returncode != 0:
            safe_err = (
                (restore_proc.stderr or b"")
                .decode("utf-8", "replace")
                .replace(pg_pass, "[REDACTED]")
            )
            raise RuntimeError(f"pg_restore failed into {temp_db}: {safe_err}")

        rst_ver, rst_counts, rst_brand_sha, rst_sched_sha, rst_job_sha, rst_owner_sha = (
            _snapshot_metrics(
                container=container,
                pg_user=pg_user,
                pg_db=temp_db,
                pg_pass=pg_pass,
            )
        )
        if src_ver != rst_ver:
            raise RuntimeError(
                f"Alembic version mismatch after restore: source={src_ver} vs restored={rst_ver}"
            )
        if src_counts != rst_counts:
            raise RuntimeError(
                f"Row count mismatch after restore: source={src_counts} vs restored={rst_counts}"
            )
        if not (
            src_brand_sha == rst_brand_sha
            and src_sched_sha == rst_sched_sha
            and src_job_sha == rst_job_sha
            and src_owner_sha == rst_owner_sha
        ):
            raise RuntimeError("Critical record checksum mismatch after restore.")

        return RestoreVerificationResult(
            dump_path=str(dump_path),
            temp_db_name=temp_db,
            source_schema_version=src_ver,
            restored_schema_version=rst_ver,
            row_counts=rst_counts,
            brand_checksum_match=src_brand_sha == rst_brand_sha,
            schedule_checksum_match=src_sched_sha == rst_sched_sha,
            job_checksum_match=src_job_sha == rst_job_sha,
            owner_checksum_match=src_owner_sha == rst_owner_sha,
        )
    finally:
        try:
            _exec_psql(
                container=container,
                pg_user=pg_user,
                pg_db="postgres",
                pg_pass=pg_pass,
                sql=f'DROP DATABASE IF EXISTS "{temp_db}" WITH (FORCE);',
            )
        except Exception as cleanup_exc:
            print(
                f"[WARN] Failed to drop temp restore DB {temp_db}: {cleanup_exc}",
                file=sys.stderr,
            )


def _find_latest_dump(backup_dir: Path) -> Path:
    dumps = sorted(backup_dir.glob("media_os_*.dump"))
    if not dumps:
        raise FileNotFoundError(f"No .dump files found in {backup_dir}")
    return dumps[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-file",
        help="Path to .dump file to verify (defaults to latest in backup dir).",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help="Backup directory used when --dump-file is omitted.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="External runtime .env file holding PostgreSQL credentials.",
    )
    args = parser.parse_args()

    try:
        dump_path = (
            Path(args.dump_file) if args.dump_file else _find_latest_dump(Path(args.backup_dir))
        )
        result = verify_restore_in_temp_db(
            dump_path=dump_path,
            env_file=Path(args.env_file) if args.env_file else None,
        )
    except Exception as exc:
        print(f"[RESTORE-VERIFY-FAIL] {exc}", file=sys.stderr)
        return 1

    print(
        f"[OK] Verified restore in isolated temp DB '{result.temp_db_name}' "
        f"(schema={result.restored_schema_version}, "
        f"brands_sha_match={result.brand_checksum_match}, rows={result.row_counts})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
