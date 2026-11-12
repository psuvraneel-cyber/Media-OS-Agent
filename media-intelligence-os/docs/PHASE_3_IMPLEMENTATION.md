# Media Intelligence OS — Phase 3 Implementation Summary

**Status:** Implemented & Verified (2026-11-10)
**Scope:** Custom Application Skeleton (`apps/web` Next.js 16 + `apps/api` FastAPI), single-owner bootstrap authentication, session/CSRF protection, structured logging, isolated Docker deployment, and UI/API/integration tests.
**Non-Scope (Deferred):** Application PostgreSQL, Redis, Celery/Beat, DB migrations, research ingestion connectors, Gemini Developer API, TTS, carousels, FFmpeg renders, Postiz write publishing, social OAuth, analytics ingestion, and custom MCP tools remain **not implemented** until Phase 4+.

---

## 1. Backend Architecture (`apps/api`)

- **Framework & Pinned Dependencies:** Python `3.12` (`pyproject.toml`): `fastapi==0.162.2`, `uvicorn==0.54.0`, `pydantic==2.14.3`, `pydantic-settings==2.16.2`, `argon2-cffi==25.1.0`, `httpx==0.28.1`.
- **Phase 1 Baseline Preserved:** [`apps/api/src/media_os_api/health.py`](../apps/api/src/media_os_api/health.py) remains untouched and dependency-free so `infra/docker/Dockerfile.baseline`, `infra/docker/compose.baseline.yml`, and `tests/smoke` continue to pass unchanged.
- **Health & Readiness:**
  - `GET /health/live` returns process liveness without auth (`200 OK`).
  - `GET /health/ready` verifies initialized configuration/auth runtime and **honestly** reports `persistent_storage: false`, `database: not_implemented (phase-4)`, and `redis: not_implemented (phase-4)`.
- **Authentication, Session & CSRF Security (`apps/api/src/media_os_api/auth/`):**
  - **Fail-closed startup:** `Settings` (`config.py`) refuses startup (`ConfigurationError`) unless `OWNER_USERNAME`, `OWNER_PASSWORD_HASH` (or `OWNER_PASSWORD_HASH_B64`), and `SESSION_SECRET` (`>= 32` high-entropy characters, non-placeholder) are valid. `COOKIE_SECURE=false` is strictly rejected unless all `CORS_ALLOWED_ORIGINS` are loopback origins (`127.0.0.1` / `localhost`).
  - **Docker Compose `$` Escape Safety:** Argon2id strings contain `$` delimiters (`$argon2id$v=...`) which Docker Compose variable interpolation strips unless escaped. `bootstrap.py` writes `OWNER_PASSWORD_HASH_B64` (Base64-encoded Argon2id hash) into external ACL-protected runtime `.env` files, and `config.py` validates both formats identically.
  - **Password Verification:** `argon2-cffi` Argon2id verification plus timing-safe dummy verification on unknown usernames.
  - **Sessions & Revocation:** HMAC-SHA256 signed cookies (`media_os_session`, `HttpOnly`, `SameSite=Lax`) paired with double-submit `media_os_csrf` token verified against `X-CSRF-Token` and `Origin` allowlists on all mutating routes. `POST /api/v1/auth/logout` records the token `jti` in a bounded server-side `SessionRevocationStore` so replayed cookies fail `401 SESSION_INVALID`.
  - **Brute-Force Guard:** Bounded sliding-window `LoginRateLimiter` keyed on both client host and normalized username bucket (`429 RATE_LIMITED` + `Retry-After`).
- **Observability & Redaction (`apps/api/src/media_os_api/observability/`):**
  - Single-line JSON stdout logger with `request_id` context binding (`X-Request-ID`), method/route/status/duration/error_category fields, and regex/key redaction stripping passwords, hashes, bearer tokens, cookies, and API keys.
  - Bounded in-memory `RequestTelemetryStore` (non-persistent) backing `GET /api/v1/system/logs`.
- **Prototype APIs & Safe Postiz Check:**
  - `GET /api/v1/system/status`: authenticated boundary matrix + read-only `httpx` check against `POSTIZ_BASE_URL/api/auth/can-register` (requires zero Postiz API keys in Phase 3).
  - `GET /api/v1/brands`, `GET /api/v1/brands/{brand_id}`, `GET /api/v1/channels`: strictly serve non-persistent `phase3_prototype_fixture` summaries for **Brand 1 (`brand-tech-ai`: Technology & AI)** and **Brand 2 (`brand-app-promo`: App Promotion, intentionally unconfigured placeholder)**.

---

## 2. Frontend Architecture (`apps/web`)

- **Stack:** Next.js `16.4.6` (App Router, `output: "standalone"`), React `19.3.1`, TypeScript `5.9.3`, Tailwind CSS `4.3.5`, `lucide-react` `1.71.0`, ESLint `9.39.5`, Vitest `5.1.0` + React Testing Library `16.3.3` (`npm` + `package-lock.json`).
- **BFF API Proxy & Server Auth Gating:**
  - `apps/web/src/app/api/v1/[...path]/route.ts` forwards same-origin `/api/v1/*` browser requests server-side to internal `INTERNAL_API_BASE_URL` (never `NEXT_PUBLIC_*`), avoiding cross-origin `localhost:3000` vs `127.0.0.1:8000` cookie splits, enforcing path-segment validation, and keeping zero secrets or tokens in browser `localStorage`.
  - `apps/web/src/app/(shell)/layout.tsx` validates `/api/v1/auth/me` server-side on every request and redirects unauthenticated visitors to `/login`.
- **12 Operator Routes:**
  `/dashboard`, `/research`, `/topics`, `/studio`, `/assets`, `/calendar`, `/approvals`, `/analytics`, `/agents`, `/integrations`, `/settings`, and `/logs` (plus `/login` and `/api/health`).
- **Multi-Brand Prototype Isolation:**
  - `BrandSwitcher` switches between `brand-tech-ai` (**Technology & AI**) and `brand-app-promo` (**App Promotion — Unconfigured Placeholder**) while preserving URL `?brand=...` scope and filtering `/integrations` channel slots cleanly per brand. Zero app logos, names, features, or fake analytics were invented.

---

## 3. Isolated Docker Topology & Measured Resource Footprint

- **Compose Stack:** [`infra/docker/compose.app.yml`](../infra/docker/compose.app.yml) (`name: media-intelligence-os`, bridge network `media-os-net`, completely separate from `postiz-network`, `postiz-temporal-network`, and `media-os-baseline-net`).
- **Hardening:** Pinned `python:3.12-slim` and `node:22-alpine` OCI digests, non-root `appuser` / `nextjs` container users, `read_only: true` root filesystems + `tmpfs: [/tmp]`, `no-new-privileges:true`, strict `127.0.0.1:8000` and `127.0.0.1:3000` loopback binds, and `mem_limit: 256m` per container.
- **Measured Incremental Memory Profile (2026-11-10):**
  - **Stage 0 (Postiz idle baseline):** 8 `postiz-runtime` containers `~2.29 GiB` total; WSL `2.8G` used / `4.6G` available; Windows host `3.85 GiB` free physical RAM.
  - **Stage 1 (+ `media-os-api`):** `media-os-api` `45 MiB / 256 MiB` (`0.13%` CPU); WSL `2.9G` used / `4.5G` available; Windows host `3.31 GiB` free.
  - **Stage 2 (+ `media-os-web` right after build):** `media-os-web` `40.97 MiB / 256 MiB`; `media-os-api` `50.58 MiB / 256 MiB`; WSL `3.0G` used / `4.3G` available.
  - **Stage 3 (Stabilized with all 10 containers healthy & active Postiz check):** `media-os-web` `56.57 MiB / 256 MiB`; `media-os-api` `40.8 MiB / 256 MiB`; WSL `3.2G` used / `4.2G` available (`884.0K` swap used); Windows host `1.47 GiB` free during concurrent container load. Combined Phase 3 overhead is `< 100 MiB` RAM steady state.

---

## 4. Rotating Local Owner Credentials

Real runtime credentials are kept outside Git at `C:\Media OS Agent\media-os-runtime\.env` (NTFS ACL locked). To set your personal owner username/password cleanly without exposing secrets in chat or terminal logs:

```powershell
cd "C:\Media OS Agent\media-intelligence-os"
.venv\Scripts\python.exe -m media_os_api.auth.bootstrap --username owner --output "C:\Media OS Agent\media-os-runtime\.env"
docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml up -d --wait --force-recreate media-os-api
```
