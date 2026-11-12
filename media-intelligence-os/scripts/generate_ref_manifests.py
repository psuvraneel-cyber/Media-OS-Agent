"""Generate and verify read-only SHA-256 manifests for reference repositories.

Keeps extracted upstream ZIP snapshots (`postiz-app-main` and `socialai-agent-main`)
strictly verifiable as unmodified references outside the custom app Git tree.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

_ws_env = os.environ.get("MEDIA_OS_WORKSPACE_ROOT")
WORKSPACE_ROOT = Path(_ws_env).resolve() if _ws_env else Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = REPO_ROOT / "references" / "manifests"

EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "logs",
    "out",
}


def get_reference_repos(workspace_root: Path | None = None) -> dict[str, Path]:
    """Return dictionary of reference repository names and resolved directory paths."""
    ws = workspace_root or WORKSPACE_ROOT
    return {
        "postiz-app-main": ws / "postiz-app-main",
        "socialai-agent-main": ws / "socialai-agent-main",
    }


REFERENCE_REPOS: dict[str, Path] = get_reference_repos()


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 digest of a file in chunks."""
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_manifest_lines(repo_dir: Path) -> list[str]:
    """Return sorted `<sha256>  <relative_posix_path>` lines for a directory."""
    if not repo_dir.is_dir():
        raise FileNotFoundError(f"Reference directory not found: {repo_dir}")

    entries: list[tuple[str, str]] = []
    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        entries.append((rel.as_posix(), compute_sha256(path)))

    entries.sort(key=lambda item: item[0])
    return [f"{digest}  {rel_path}" for rel_path, digest in entries]


def write_manifests() -> int:
    """Write SHA-256 manifests for all configured reference directories."""
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    for name, repo_dir in REFERENCE_REPOS.items():
        lines = collect_manifest_lines(repo_dir)
        out_path = MANIFEST_DIR / f"{name}.sha256"
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[manifest] wrote {len(lines)} entries -> {out_path}")
    return 0


def verify_manifests(*, workspace_root: Path | None = None, allow_missing: bool = False) -> int:
    """Verify current files on disk match recorded SHA-256 manifests."""
    failures = 0
    repos = get_reference_repos(workspace_root)
    is_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"} or os.environ.get(
        "GITHUB_ACTIONS", ""
    ).lower() in {"1", "true", "yes"}

    for name, repo_dir in repos.items():
        manifest_path = MANIFEST_DIR / f"{name}.sha256"
        if not manifest_path.is_file():
            print(f"[ERROR] missing manifest: {manifest_path}", file=sys.stderr)
            failures += 1
            continue

        if not repo_dir.is_dir():
            if is_ci or allow_missing:
                print(
                    f"[SKIP] {name}: directory not present: {repo_dir} "
                    f"(external reference snapshots not co-located in this environment)"
                )
                continue
            print(f"[ERROR] reference directory not found: {repo_dir}", file=sys.stderr)
            failures += 1
            continue

        expected = manifest_path.read_text(encoding="utf-8").splitlines()
        actual = collect_manifest_lines(repo_dir)
        if expected != actual:
            print(
                f"[ERROR] reference directory mutated or drifted: {repo_dir} "
                f"(expected {len(expected)} files, got {len(actual)})",
                file=sys.stderr,
            )
            failures += 1
        else:
            print(f"[OK] {name}: {len(actual)} files match SHA-256 manifest")
    return 1 if failures else 0


def main() -> int:
    """CLI entry point for generating or verifying reference SHA-256 manifests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify reference directories match saved manifests without modifying files.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Override workspace root directory where reference snapshots reside.",
    )
    parser.add_argument(
        "--if-present",
        action="store_true",
        help="Explicitly skip and report missing reference directories instead of failing.",
    )
    args = parser.parse_args()
    if args.verify:
        return verify_manifests(
            workspace_root=args.workspace_root,
            allow_missing=args.if_present,
        )
    return write_manifests()


if __name__ == "__main__":
    raise SystemExit(main())
