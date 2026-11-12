"""Deliberate local CLI utility for rotating the persisted single-owner password."""

from __future__ import annotations

import argparse
import base64
import getpass
import os
from pathlib import Path

from media_os_api.auth.persistence import OwnerIdentityError, rotate_owner_password
from media_os_api.config import load_settings
from media_os_api.db.session import (
    create_db_engine_from_settings,
    create_session_factory,
    session_scope,
)


def _update_external_env_hash(env_path: Path, new_password_hash: str) -> None:
    if not env_path.is_file():
        raise SystemExit(f"Runtime env file not found: {env_path}")
    hash_b64 = base64.b64encode(new_password_hash.encode("utf-8")).decode("ascii")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("OWNER_PASSWORD_HASH_B64="):
            updated.append(f"OWNER_PASSWORD_HASH_B64={hash_b64}")
            replaced = True
        elif line.startswith("OWNER_PASSWORD_HASH="):
            continue
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"OWNER_PASSWORD_HASH_B64={hash_b64}")
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _load_env_file_into_os(env_path: Path) -> None:
    if not env_path.is_file():
        raise SystemExit(f"Runtime env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _is_within_repo(path: Path) -> bool:
    return any(
        (ancestor / ".git").exists()
        or ((ancestor / "pyproject.toml").is_file() and (ancestor / "alembic.ini").is_file())
        for ancestor in (path, *path.parents)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rotate single-owner password in PostgreSQL and invalidate prior sessions."
    )
    parser.add_argument("--username", required=True, help="Persisted owner username.")
    parser.add_argument(
        "--env-file",
        help="Optional external runtime env file (outside Git) to load DB config and update hash.",
    )
    args = parser.parse_args()

    if not args.username.strip():
        raise SystemExit("Owner username must not be blank.")

    env_path: Path | None = None
    if args.env_file:
        raw_path = Path(args.env_file).absolute()
        env_path = raw_path.resolve()
        if raw_path.name == ".env.example" or env_path.name == ".env.example":
            raise SystemExit("Refusing to read or update .env.example template file.")
        if _is_within_repo(raw_path) or _is_within_repo(env_path):
            raise SystemExit(
                "Refusing to read or update runtime secrets inside the Git repository."
            )
        _load_env_file_into_os(env_path)

    new_password = getpass.getpass("Enter new owner password (min 12 chars recommended): ").rstrip(
        "\r"
    )
    confirm = getpass.getpass("Confirm new owner password: ").rstrip("\r")
    if new_password != confirm:
        raise SystemExit("Passwords did not match.")
    if len(new_password) < 8:
        raise SystemExit("Password must be at least 8 characters long.")

    settings = load_settings()
    engine = create_db_engine_from_settings(settings)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as db:
            owner = rotate_owner_password(
                db,
                username=args.username,
                new_password=new_password,
                actor_id="cli:rotate_password",
            )
            new_hash = owner.password_hash
            new_version = owner.token_version
    except OwnerIdentityError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        engine.dispose()

    if env_path is not None:
        _update_external_env_hash(env_path, new_hash)
        print(
            f"Rotated password for '{args.username}' in PostgreSQL (token_version={new_version}) "
            f"and updated external runtime env."
        )
    else:
        print(
            f"Rotated password for '{args.username}' in PostgreSQL (token_version={new_version}). "
            "All prior sessions have been revoked."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
