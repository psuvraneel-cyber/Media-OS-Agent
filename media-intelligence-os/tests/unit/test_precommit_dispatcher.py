"""Unit tests verifying pre-commit environment dispatcher and manifest verification."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.generate_ref_manifests import verify_manifests
from scripts.run_in_env import ALLOWED_TOOLS, main, resolve_tool_path

pytestmark = pytest.mark.unit


def test_allowed_tools_whitelist() -> None:
    """Only ruff, mypy, and python are permitted tools."""
    assert frozenset({"ruff", "mypy", "python"}) == ALLOWED_TOOLS


def test_unauthorized_tool_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Requesting an unauthorized tool must abort execution with exit code 1."""
    monkeypatch.setattr(sys, "argv", ["run_in_env.py", "curl", "--version"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Tool 'curl' is not an authorized tool" in captured.err


def test_resolve_local_venv_tools() -> None:
    """Project-local virtual environment tools must resolve accurately."""
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".venv").is_dir():
        pytest.skip("No project-local .venv (CI installs into the runner's Python).")
    for tool in ("ruff", "mypy", "python"):
        resolved = resolve_tool_path(repo_root, tool)
        path_obj = Path(resolved)
        assert path_obj.is_file(), f"Tool {tool} not found at {path_obj}"
        assert ".venv" in path_obj.parts


def test_missing_tool_in_venv_raises_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If .venv exists but tool binary is missing, fail with explicit instruction."""
    fake_venv = tmp_path / ".venv"
    is_windows = sys.platform == "win32"
    bin_dir = fake_venv / ("Scripts" if is_windows else "bin")
    bin_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc_info:
        resolve_tool_path(tmp_path, "ruff")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "missing from local virtual environment" in captured.err
    assert 'uv pip install -e ".[dev]"' in captured.err


def test_ci_fallback_when_venv_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When .venv is absent and CI=true, resolve tool from PATH or sys.executable."""
    monkeypatch.setenv("CI", "true")
    # For python, should resolve sys.executable
    resolved_py = resolve_tool_path(tmp_path, "python")
    assert resolved_py == sys.executable

    # For ruff/mypy, if on PATH or mocked
    with patch("shutil.which", return_value="/usr/bin/ruff"):
        resolved_ruff = resolve_tool_path(tmp_path, "ruff")
        assert resolved_ruff == "/usr/bin/ruff"


def test_ci_fallback_fails_if_tool_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """In CI, if tool is missing from PATH, abort cleanly without silent skips."""
    monkeypatch.setenv("CI", "true")
    with patch("shutil.which", return_value=None):
        with pytest.raises(SystemExit) as exc_info:
            resolve_tool_path(tmp_path, "ruff")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "CI environment detected, but tool 'ruff' was not found on PATH" in captured.err


def test_local_missing_venv_fails_with_init_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Outside CI, missing .venv must fail instructing user to create it."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        resolve_tool_path(tmp_path, "ruff")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Local project virtual environment not found" in captured.err
    assert "uv venv .venv --python 3.12" in captured.err


def test_manifest_verification_on_real_references() -> None:
    """In local development workspace, verify_manifests verifies all files and returns 0."""
    res = verify_manifests()
    assert res == 0


def test_manifest_verification_ci_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In CI environment with missing reference folders, verify_manifests skips cleanly."""
    monkeypatch.setenv("CI", "true")
    res = verify_manifests(workspace_root=tmp_path)
    assert res == 0


def test_manifest_verification_local_missing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """In local environment with missing reference folders, verify_manifests fails with error."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    res = verify_manifests(workspace_root=tmp_path)
    assert res == 1
    captured = capsys.readouterr()
    assert "[ERROR] reference directory not found" in captured.err
