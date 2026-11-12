"""Local CLI utility for hashing single-owner credentials and DB/Redis env outside Git.

Usage:
    python -m media_os_api.auth.bootstrap --username owner ^
      --output "C:\\Media OS Agent\\media-os-runtime\\.env"
Prompts securely with getpass (never logs plaintext passwords or secrets).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import secrets
from pathlib import Path

from media_os_api.auth.passwords import hash_password


def render_runtime_env_content(
    *,
    owner_username: str,
    owner_password_hash: str,
    session_secret: str,
    cookie_secure: bool = False,
    cors_allowed_origins: str = "http://127.0.0.1:3000,http://localhost:3000",
    postiz_base_url: str = "http://host.docker.internal:4007",
    postgres_password: str | None = None,
    redis_password: str | None = None,
    display_timezone: str = "Asia/Kolkata",
) -> str:
    """Render an external runtime `.env` payload containing Phase 4 auth, DB, and Redis config."""
    hash_b64 = base64.b64encode(owner_password_hash.encode("utf-8")).decode("ascii")
    pg_pass = postgres_password or secrets.token_hex(24)
    rd_pass = redis_password or secrets.token_hex(24)
    return "\n".join(
        [
            "# Media Intelligence OS Phase 4 Local Runtime Configuration",
            "# Generated via `python -m media_os_api.auth.bootstrap`.",
            "# Keep this file outside the Git repository and restrict OS ACL permissions.",
            "APP_ENV=production",
            "LOG_LEVEL=INFO",
            f"APP_DISPLAY_TIMEZONE={display_timezone}",
            "API_HOST=0.0.0.0",
            "API_PORT=8000",
            "WEB_PORT=3000",
            f"OWNER_USERNAME={owner_username}",
            f"OWNER_PASSWORD_HASH_B64={hash_b64}",
            f"SESSION_SECRET={session_secret}",
            "SESSION_TTL_SECONDS=28800",
            f"COOKIE_SECURE={'true' if cookie_secure else 'false'}",
            f"CORS_ALLOWED_ORIGINS={cors_allowed_origins}",
            "LOGIN_RATE_LIMIT_MAX_ATTEMPTS=5",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS=300",
            "RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE=false",
            "POSTGRES_HOST=media-os-postgres",
            "POSTGRES_PORT=5432",
            "POSTGRES_DB=media_os",
            "POSTGRES_USER=media_os_user",
            f"POSTGRES_PASSWORD={pg_pass}",
            f"DATABASE_URL=postgresql+psycopg://media_os_user:{pg_pass}@media-os-postgres:5432/media_os",
            "REDIS_HOST=media-os-redis",
            "REDIS_PORT=6379",
            f"REDIS_PASSWORD={rd_pass}",
            f"REDIS_URL=redis://:{rd_pass}@media-os-redis:6379/0",
            f"CELERY_BROKER_URL=redis://:{rd_pass}@media-os-redis:6379/0",
            f"CELERY_RESULT_BACKEND=redis://:{rd_pass}@media-os-redis:6379/1",
            f"POSTIZ_BASE_URL={postiz_base_url}",
            "INTERNAL_API_BASE_URL=http://media-os-api:8000",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap local Argon2id owner credentials for Media Intelligence OS."
    )
    parser.add_argument("--username", required=True, help="Single-owner dashboard username.")
    parser.add_argument(
        "--output",
        required=True,
        help="Destination runtime env file path OUTSIDE the Git repository.",
    )
    parser.add_argument(
        "--cookie-secure",
        action="store_true",
        help="Set COOKIE_SECURE=true (only omit for local loopback HTTP).",
    )
    args = parser.parse_args()

    out_path = Path(args.output).resolve()
    repo_root = Path(__file__).resolve().parents[4]
    if repo_root in out_path.parents or out_path == repo_root:
        raise SystemExit("Refusing to write runtime credentials inside the Git repository.")

    password = getpass.getpass("Enter single-owner password (min 12 chars recommended): ")
    confirm = getpass.getpass("Confirm single-owner password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters long.")

    password_hash = hash_password(password)
    session_secret = secrets.token_hex(32)
    content = render_runtime_env_content(
        owner_username=args.username.strip(),
        owner_password_hash=password_hash,
        session_secret=session_secret,
        cookie_secure=args.cookie_secure,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"Wrote ACL-ready runtime configuration to: {out_path}")
    print("Reminder: on Windows run `icacls` to restrict access to your user account only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
