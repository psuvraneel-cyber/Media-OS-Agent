# Reference Repositories

This directory documents the **read-only upstream reference snapshots** stored alongside the custom application repository in the parent workspace (`C:\Media OS Agent\`).

> [!IMPORTANT]
> Both reference repositories live **outside** `C:\Media OS Agent\media-intelligence-os\` and must **never** be copied into this Git repository, modified, reformatted, upgraded, or merged into our application codebase.

---

## 1. Snapshot Inventory

| Reference | Upstream URL | Local Path | Archive / VCS Status | Version / Tag Metadata | License | Inspection Date |
|---|---|---|---|---|---|---|
| **Postiz** | https://github.com/gitroomhq/postiz-app | `C:\Media OS Agent\postiz-app-main` | Extracted ZIP snapshot (no `.git` directory; commit SHA unrecoverable locally) | `version.txt` = `v1.47.0`; `package.json` `version` = `1.0.0` (monorepo `gitroom`) | **AGPL-3.0** (`LICENSE`) | 2026-11-10 |
| **SocialAI Agent** | https://github.com/pavelblank/socialai-agent | `C:\Media OS Agent\socialai-agent-main` | Extracted ZIP snapshot (no `.git` directory; commit SHA unrecoverable locally) | No version file or tag metadata in archive | Dual **MIT** (`LICENSE-MIT`) or **Apache-2.0** (`LICENSE-APACHE`) | 2026-11-10 |

Deterministic SHA-256 provenance manifests (excluding build outputs/caches if any) are stored in [`references/manifests/`](./manifests/) and generated via [`scripts/generate_ref_manifests.py`](../scripts/generate_ref_manifests.py).

---

## 2. Postiz (`postiz-app-main`) — Separate Publishing Service

### Role in Media Intelligence OS
Postiz will run as a **standalone, separate Docker Compose service** in Phase 2+, acting strictly as our social-account OAuth, scheduling, publishing, and supported analytics engine via its Public REST API (`/public/v1`) or MCP server (`/mcp`).

### Key Inspected Modules
- `docker-compose.yaml` & `var/docker/nginx.conf`: Exposes `4007:5000` (nginx proxies `/` → `4200` frontend, `/api/` → `3000` backend, `/uploads/` → `/uploads/`); includes `postiz`, `postiz-postgres` (`postgres:17-alpine`), `postiz-redis` (`redis:7.2`), `spotlight`, `temporal`, `temporal-postgresql` (`postgres:16`), `temporal-elasticsearch` (`elasticsearch:7.17.27`), `temporal-admin-tools`, `temporal-ui` (`8080`).
- `apps/backend/src/public-api/routes/v1/public.integrations.controller.ts` & `apps/backend/src/services/auth/public.auth.middleware.ts`: Public REST API (`/public/v1/upload`, `/upload-from-url`, `/posts`, `/integrations`, `/find-slot/:id`, `/analytics/:integration`, `/analytics/post/:postId`) authenticated via `Authorization` header (API key or `pos_` OAuth token).
- `libraries/nestjs-libraries/src/chat/start.mcp.ts`: Built-in MCP server (`Postiz MCP`, protocol `2026-07-28`, `@mastra/mcp`).
- `libraries/nestjs-libraries/src/integrations/social/instagram.provider.ts` & `instagram.standalone.provider.ts`: Supports Instagram Business (Facebook Page) and Standalone Instagram OAuth, carousels (up to 10 items), Reels, Stories, comments, and insights analytics.
- `libraries/nestjs-libraries/src/integrations/social/youtube.provider.ts`: Supports YouTube OAuth, resumable video uploads, and YouTube Analytics API v2.
- `libraries/nestjs-libraries/src/upload/local.storage.ts` & `cloudflare.storage.ts`: Media storage providers (`local` vs `cloudflare` R2).
- `apps/orchestrator/src/` & `libraries/nestjs-libraries/src/database/prisma/schema.prisma`: Temporal workflow orchestration and PostgreSQL schema.

### Strict Non-Reuse / Boundary Rules
- **NEVER** merge Postiz code into `media-intelligence-os` (also required to respect its AGPL-3.0 boundary).
- **NEVER** copy Postiz internal TypeScript/NestJS classes into our backend.
- **NEVER** read from or write to Postiz's PostgreSQL database or Redis instance directly.
- **NEVER** alter Postiz's Prisma schema or strip its Temporal/Elasticsearch dependencies without proving compatibility on the pinned release.

---

## 3. SocialAI Agent (`socialai-agent-main`) — Read-Only Architectural Reference

### Role in Media Intelligence OS
SocialAI Agent is a **reference implementation only** for studying local automation patterns. It is **not** production-ready and will **not** be used as our application foundation.

### Useful Patterns to Study Selectively
- `scripts/research.py`: Topic normalization (`clean()`), keyword-overlap near-duplicate detection (`_keywords`, `_is_near_duplicate`, `check_fresh` using 0.6 overlap threshold), and hook pattern scoring (`HOOK_SCORE`).
- `scripts/scheduler.py`: Daily posting-time jitter (`_jittered_target`) to avoid robotic fixed-second cadence, and growth-vs-steady schedule phasing (`db.schedule_phase`).
- `scripts/make_video.py`: FFmpeg vertical video assembly pipeline (`zoompan` Ken Burns motion, ASS/VTT captions, `xfade`/`acrossfade` scene transitions, and procedural ambient music synthesis via `sine`/`tremolo`/`anoisesrc`/`aecho`).
- `scripts/learn.py`: Minimum sample threshold (`MIN_SAMPLES = 15`) and bounded category multiplier clamping (`CLAMP = (0.5, 2.0)`) to avoid overfitting early view-count noise.
- `scripts/youtube.py`: Resumable upload error classification (quota `403`, auth `401`, transient retries) and `containsSyntheticMedia` metadata flag.

### Components That Must NOT Be Reused
- `scripts/meta.py`: **Does NOT implement Instagram publishing** (Facebook Page only; explicitly notes Instagram requires public media URLs). Do **not** use for Instagram.
- `scripts/db.py`: Ad-hoc SQLite (`data/brain.db`) storage; unsuitable for our multi-worker PostgreSQL + Celery architecture.
- Local Ollama inference (`127.0.0.1:11434`): Violates our 16 GB laptop constraint; we use remote Gemini Developer API instead.
- `scripts/research.py` `from_reddit()`: Reddit is **BLOCKED** pending permitted-use and Data API Terms review.
- `scripts/dashboard.py` & `scripts/autopilot.py`: Monolithic stdlib HTTP server + subprocess runner with unauthenticated `auto` mode by default in `rules.json`; replaced by Next.js + FastAPI + explicit approval workflows.
