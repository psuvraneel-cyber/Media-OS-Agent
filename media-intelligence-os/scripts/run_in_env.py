"""Cross-platform executable dispatcher for pre-commit hooks and local tooling.

Resolves project-local executables from .venv on Windows and Unix, guaranteeing
reproducible tool execution regardless of host PATH configuration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ALLOWED_TOOLS: frozenset[str] = frozenset({"ruff", "mypy", "python"})


def _is_ci_environment() -> bool:
    """Return True if running inside a known continuous integration environment."""
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"} or os.environ.get(
        "GITHUB_ACTIONS", ""
    ).lower() in {"1", "true", "yes"}


def resolve_tool_path(repo_root: Path, tool: str) -> Path | str:
    """Resolve executable path for the requested tool following strict resolution order.

    Resolution Order:
    1. Local virtual environment (.venv/Scripts on Windows, .venv/bin on Unix).
    2. If .venv exists but tool is missing, fail with explicit installation instruction.
    3. If .venv does not exist and running in CI, resolve from system PATH.
    4. If .venv does not exist and not in CI, fail with setup instruction.
    """
    if tool not in ALLOWED_TOOLS:
        print(
            f"Error: Tool '{tool}' is not an authorized tool. "
            f"Allowed tools: {', '.join(sorted(ALLOWED_TOOLS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    custom_venv = os.environ.get("MEDIA_OS_VENV_PATH") or os.environ.get("VIRTUAL_ENV")
    venv_dir = Path(custom_venv).resolve() if custom_venv else (repo_root / ".venv")
    is_windows = sys.platform == "win32"
    bin_dir = venv_dir / ("Scripts" if is_windows else "bin")

    if venv_dir.is_dir():
        if not bin_dir.is_dir():
            print(
                f"Error: Virtual environment exists at '{venv_dir}' but binary directory "
                f"'{bin_dir}' is missing.\n"
                f"Recreate the environment:\n"
                f"  uv venv .venv --python 3.12\n"
                f'  uv pip install -e ".[dev]"',
                file=sys.stderr,
            )
            sys.exit(1)

        if tool == "python":
            cand = bin_dir / ("python.exe" if is_windows else "python")
            if cand.is_file():
                return cand
            print(
                f"Error: Python interpreter missing from virtual environment at '{bin_dir}'.\n"
                f"Reinstall the virtual environment:\n"
                f"  uv venv .venv --python 3.12\n"
                f'  uv pip install -e ".[dev]"',
                file=sys.stderr,
            )
            sys.exit(1)

        # For ruff and mypy
        candidate = bin_dir / (f"{tool}.exe" if is_windows else tool)
        if candidate.is_file():
            return candidate

        print(
            f"Error: Pinned tool '{tool}' is missing from local virtual environment "
            f"at '{bin_dir}'.\n"
            f"Install development dependencies:\n"
            f'  uv pip install -e ".[dev]"\n'
            f'  (or: .\\{bin_dir.relative_to(repo_root)}\\python.exe -m pip install -e ".[dev]")',
            file=sys.stderr,
        )
        sys.exit(1)

    # .venv does not exist
    if _is_ci_environment():
        if tool == "python":
            return sys.executable

        which_path = shutil.which(tool)
        if which_path is not None:
            return which_path

        print(
            f"Error: CI environment detected, but tool '{tool}' was not found on PATH.\n"
            f'Ensure pip install -e ".[dev]" has executed in the CI workflow step.',
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Error: Local project virtual environment not found at '{venv_dir}'.\n"
        f"Initialize the project environment:\n"
        f"  uv venv .venv --python 3.12\n"
        f'  uv pip install -e ".[dev]"',
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    """Dispatch the tool execution with arguments, forwarding exit code and streams."""
    if len(sys.argv) < 2:
        print(
            "Usage: python scripts/run_in_env.py <tool_name> [args...]\n"
            f"Allowed tools: {', '.join(sorted(ALLOWED_TOOLS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    tool = sys.argv[1]
    args = sys.argv[2:]

    repo_root = Path(__file__).resolve().parent.parent
    tool_exec = resolve_tool_path(repo_root, tool)

    try:
        proc = subprocess.run([str(tool_exec), *args], check=False)
        sys.exit(proc.returncode)
    except OSError as exc:
        print(f"Error executing '{tool_exec}': {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
