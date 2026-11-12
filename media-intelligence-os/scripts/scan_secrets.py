"""Lightweight secret and forbidden-file scanner for Media Intelligence OS.

Runs locally, in pre-commit, and in CI without external paid tools or API keys.
Never prints matched secret values to stdout/stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
}

FORBIDDEN_FILENAMES = {
    ".env",
    "connections.json",
    "youtube_client.json",
    "youtube_token.json",
    "client_secret.json",
}

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("google_api_key", re.compile("AI" + r"za[0-9A-Za-z_-]{35}")),
    ("openai_or_generic_sk", re.compile("sk-" + r"[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile("gh" + r"p_[A-Za-z0-9]{30,}")),
    ("github_fine_pat", re.compile("github_pat_" + r"[A-Za-z0-9_]{20,}")),
    ("slack_token", re.compile("xox" + r"[baprs]-[0-9A-Za-z-]{10,}")),
    ("postiz_oauth_token", re.compile("pos_" + r"[A-Za-z0-9]{16,}")),
    ("private_key_pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key_id", re.compile("AK" + r"IA[0-9A-Z]{16}")),
]

PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "VERIFY_IN_PHASE_")


def scan_repo(root: Path = REPO_ROOT) -> list[str]:
    """Return a list of sanitized security finding descriptions."""
    findings: list[str] = []

    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not path.is_file():
            continue

        name = path.name
        if name in FORBIDDEN_FILENAMES or (name.startswith(".env.") and name != ".env.example"):
            findings.append(
                f"Forbidden credential/env file present in repository: {rel.as_posix()}"
            )
            continue

        if rel.parts[:2] == ("references", "manifests"):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".mp4", ".pyc", ".db", ".sqlite"}:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        f"Potential secret ({label}) detected at {rel.as_posix()}:{line_no}"
                    )

        if name == ".env.example":
            for line_no, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                val = value.strip().strip('"').strip("'")
                is_secret_var = key.endswith(("_KEY", "_SECRET", "_PASSWORD", "_TOKEN"))
                if is_secret_var and val and not val.startswith(PLACEHOLDER_PREFIXES):
                    findings.append(f"Non-placeholder value for {key} in .env.example:{line_no}")
                dsn_keys = {
                    "DATABASE_URL",
                    "REDIS_URL",
                    "CELERY_BROKER_URL",
                    "CELERY_RESULT_BACKEND",
                }
                if key in dsn_keys and "@" in val and "REPLACE_WITH_" not in val:
                    findings.append(f"Non-placeholder password in {key}:{line_no}")

    return findings


def main() -> int:
    """CLI entry point returning non-zero on any secret hygiene finding."""
    findings = scan_repo()
    if findings:
        for item in findings:
            print(f"[SECRET-SCAN-FAIL] {item}", file=sys.stderr)
        return 1
    print("[OK] Secret scan passed: zero leaked credentials or forbidden env files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
