# Contributing & Development Setup

Media Intelligence OS enforces strict reproducibility, type safety, code formatting, and secret-hygiene checks across every phase.

---

## 1. Prerequisites

Verified baseline versions on Windows 11 (see [`docs/ENVIRONMENT.md`](./docs/ENVIRONMENT.md)):
- **Python 3.12** (`3.12.9` verified; pinned because host `python` may resolve to `3.14.x`)
- **uv** (`0.10.9`+) or standard `venv`
- **Docker Desktop** (`4.99.0`+ / Compose `v5.7.0`+)
- **Git** (`2.55.0`+)

---

## 2. Local Python Environment Setup (Windows PowerShell)

Create an isolated virtual environment pinned to **Python 3.12**:

```powershell
# Using uv (recommended)
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

# Or using Python launcher directly
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

---

## 3. Code Quality, Type Checking & Security Checks

Run all checks before committing:

```powershell
# 1. Check formatting (Ruff)
.\.venv\Scripts\ruff.exe format --check .

# 2. Run static linting (Ruff)
.\.venv\Scripts\ruff.exe check .

# 3. Run strict static type checking (MyPy)
.\.venv\Scripts\mypy.exe

# 4. Scan repository for accidental secrets or unignored .env files
.\.venv\Scripts\python.exe scripts/scan_secrets.py
```

Auto-format code when needed:

```powershell
.\.venv\Scripts\ruff.exe format .
```

---

## 4. Running Tests

```powershell
# Unit tests (fast, no Docker required)
.\.venv\Scripts\pytest.exe tests/unit

# Integration tests: ONLY against the disposable stack (the suite refuses the runtime DB/Redis)
.\.venv\Scripts\python.exe scripts/run_integration_tests.py

# Smoke tests (builds, starts, health-checks, stops, and restarts Phase 1 Docker baseline)
.\.venv\Scripts\pytest.exe tests/smoke
```

---

## 5. Pre-Commit Hooks

Install Git pre-commit hooks locally:

```powershell
.\.venv\Scripts\pre-commit.exe install
.\.venv\Scripts\pre-commit.exe run --all-files
```

Hooks execute automatically on `git commit` via the cross-platform dispatcher `scripts/run_in_env.py`, ensuring `ruff`, `mypy`, secret scanning, and reference manifest verification execute against `.venv` without requiring global PATH activation.

---

## 6. Reference Repository Integrity

The upstream reference snapshots (`..\postiz-app-main` and `..\socialai-agent-main`) are **strictly read-only**. Verify they remain unmodified at any time:

```powershell
.\.venv\Scripts\python.exe scripts/generate_ref_manifests.py --verify
```
