# Development Environment Inventory

Verified on **2026-11-10** on the target Windows workstation prior to building Phase 2+ services. All values below are from actual command output observed on this machine.

## 1. Operating System & Hardware

| Component | Verified Value / Source |
|---|---|
| **OS Edition** | Microsoft Windows 11 Home Single Language |
| **OS Version / Build** | `10.0.26300` (DisplayVersion `26H2`, ReleaseId `2009`, UBR `9711`) |
| **Architecture** | 64-bit (`x86_64`) |
| **CPU (Host Spec)** | Intel Core i5-13420H (12 logical CPUs visible to Docker Engine) |
| **Host RAM** | 16 GB DDR5 (`TotalVisibleMemorySize`: `16,462,712 KB` ≈ 15.70 GiB; `FreePhysicalMemory` at check: `3,945,176 KB` ≈ 3.76 GiB) |
| **GPU (Host Spec)** | Integrated Intel graphics (no dedicated GPU assumption) |
| **Disk (`C:\`)** | `606.07 GB` used, **`345.57 GB` free** |

## 2. Core Tools & Runtimes

| Tool | Verified Version / Details | Path / Notes |
|---|---|---|
| **Antigravity** | `2.30.0` (app `version` file `44.9.1`; `Antigravity IDE (User)` `2.6.0`) | `%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe` (dev IDE only; production runtime is Docker) |
| **Git** | `git version 2.55.0.windows.3` | On `PATH` |
| **Docker Desktop** | `4.99.0` | Windows uninstall registry (`HKCU`) |
| **Docker Engine** | `29.9.3` (build `5457199`, `OSType=linux`, `Driver=overlayfs`) | Client & Server `29.9.3` |
| **Docker Compose** | `v5.7.0` | `docker compose version` |
| **WSL** | `2.7.13.0` (Kernel `6.18.33.2-2`, WSLg `1.0.73.2`) | Distros: `docker-desktop` (WSL 2, Running), `OmniRushBox` (WSL 2, Stopped), `PokeBuild` (WSL 1, Stopped) |
| **Python (Default on PATH)** | `Python 3.14.6` | `C:\Python314\python.exe` |
| **Python (Project Baseline)** | `Python 3.12.9` | `C:\Users\Sauvraneel Paul\AppData\Local\Programs\Python\Python312\python.exe` (pinned for `.venv` & Docker `python:3.12-slim`) |
| **Python (Other Installed)** | `3.13-32`, `3.11` | Discovered via `py -0p` |
| **uv** | `uv 0.10.9 (f675560f3 2026-03-06)` | `C:\Users\Sauvraneel Paul\.local\bin\uv.exe` |
| **Node.js** | `v26.13.0` | `C:\Program Files\nodejs\node.exe` |
| **npm** | `11.20.0` | `C:\Program Files\nodejs\npm.cmd` |
| **pnpm** | `12.18.0` | `%APPDATA%\npm\pnpm.cmd` |
| **FFmpeg** | `ffmpeg version 9.0.2-full_build-www.gyan.dev` | Installed via WinGet (`Gyan.FFmpeg`) |

## 3. Docker Resource Configuration

- **Docker Engine Allocated Memory (`MemTotal`):** `8,166,977,536` bytes (**~7.61 GiB**)
- **Docker Engine Logical CPUs (`NCPU`):** `12`
- **User `.wslconfig`:** Not present at `%USERPROFILE%\.wslconfig` (WSL2 / Docker Desktop using default ~50% host memory allocation).
- **Pre-existing Containers / Compose Projects / Volumes:** None (`docker compose ls --all` empty, `docker ps -a` empty, `docker volume ls` empty).
- **Pre-existing Docker Networks:** Default `bridge`, `host`, `none` only.

## 4. Compatibility Notes & Prerequisite Observations

1. **WSL Version:** `2.7.13.0` exceeds the recommended minimum (`2.1.5+`).
2. **Disk Space:** `345.57 GB` free on `C:\` is sufficient for Docker images, PostgreSQL data, and local media rendering, though active disk cleanup/retention policies will be enforced in Phase 3+.
3. **Python 3.14 vs 3.12:** Host `python` resolves to `3.14.6`, which can cause wheel incompatibility with future ML/media/async libraries. Therefore, Media Intelligence OS pins **Python 3.12** (`3.12.9` locally via `py -3.12` / `uv venv --python 3.12`, and `python:3.12-slim` in Docker).
4. **Docker RAM Budget Verified Through Phase 4:** Docker Desktop's WSL2 engine sees ~7.61 GiB RAM. `postiz-runtime` (8 containers) plus all 6 Phase 4 Media OS containers (`media-os-postgres`, `media-os-redis`, `media-os-api`, `media-os-worker`, `media-os-scheduler`, `media-os-web`) run concurrently with Media OS consuming **`~279.9 MiB` steady-state RSS** (`3.6 GiB` WSL used / `3.8 GiB` available, `14/14` healthy; `docs/PHASE_4_IMPLEMENTATION.md`).
5. **Phase 3–4 Pinned Base Images & Frameworks:**
   - Docker Python base: `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` (`fastapi==0.162.2`, `uvicorn==0.54.0`, `pydantic==2.14.3`, `pydantic-settings==2.16.2`, `argon2-cffi==25.1.0`, `httpx==0.28.1`, `sqlalchemy==2.0.57`, `alembic==1.20.1`, `psycopg[binary]==3.3.8`, `redis==8.1.0`, `celery==5.7.0`, `packaging==26.3`, `tzdata==2026.6`).
   - Docker Node base: `node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202` (`next@16.4.6` standalone, `react@19.3.1`, `tailwindcss@4.3.5`, `typescript@5.9.3`, `vitest@5.1.0`).
   - Database & Broker images: `postgres:17-alpine@sha256:3540c0362c809df084ffac0ae69fe794515f9de92692f02178bcdce0ef0302ac`, `redis:7.2@sha256:5b816bb37d9707a70f10ef5002ecf8ee431750d397010fc4314c93dca887962f`.
6. **No Automatic Host Modifications:** No global Windows packages or system configurations were altered during Phase 0–4; runtime credentials live outside Git in `C:\Media OS Agent\media-os-runtime\.env` and backups in `C:\Media OS Agent\media-os-backups\`.
