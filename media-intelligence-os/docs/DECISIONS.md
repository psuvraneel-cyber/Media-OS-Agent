# Architecture Decision Records (ADRs) — Media Intelligence OS

Statuses used in this log:
- **`ACCEPTED`**: Explicitly locked by the project contract / non-negotiable architectural rules.
- **`PROPOSED`**: Recommended architectural baseline or provisional default awaiting explicit owner sign-off (`OWNER_CONFIRMATION_REQUIRED`).
- **`SUPERSEDED`**: Replaced by a later decision.

---

## ADR-001: Custom Application vs. Direct Postiz Modification
- **Decision ID:** ADR-001
- **Decision Title:** Build Custom Intelligence Engine Separate from Postiz Source Code
- **Date:** 2026-11-10
- **Context:** Postiz (`v1.47.0`) is a mature AGPL-3.0 NestJS + Next.js + Temporal social-media scheduler. We also need technical research intelligence, claim verification, Gemini creative production, FFmpeg video rendering, and domain QA.
- **Options Considered:**
  1. Fork and modify Postiz directly to embed research, Gemini generation, and FFmpeg pipelines.
  2. Build a separate custom application (`media-intelligence-os`) that communicates with an unmodified Postiz instance over supported APIs.
- **Selected Approach:** Option 2 — Build `media-intelligence-os` as an independent repository and service stack.
- **Reason:** Modifying Postiz directly creates high upstream merge friction, couples research/creative logic to Postiz's Prisma schema and Temporal orchestrator, and triggers AGPL-3.0 source-coupling risks.
- **Consequences:** Clean domain separation, independent technology choices (Python/FastAPI for AI/media pipelines), and zero Postiz schema drift; requires maintaining an API client adapter against Postiz `/public/v1` or MCP.
- **Status:** `ACCEPTED`

---

## ADR-002: Separate Postiz Deployment (Compose, DB, Redis, Temporal, Volumes)
- **Decision ID:** ADR-002
- **Decision Title:** Isolate Postiz Infrastructure in Its Own Docker Compose Stack
- **Date:** 2026-11-10
- **Context:** Postiz depends on its own PostgreSQL (`postgres:17-alpine`), Redis (`redis:7.2`), Temporal server/UI, `temporal-postgresql`, `temporal-elasticsearch`, and persistent volumes (`postiz-config`, `postiz-uploads`, `postgres-volume`, `postiz-redis-data`).
- **Options Considered:**
  1. Share PostgreSQL/Redis containers between Media Intelligence OS and Postiz.
  2. Strip Temporal/Elasticsearch from Postiz upfront to reduce memory.
  3. Keep Postiz in its own Compose project, databases, networks, and volumes with all required dependencies intact.
- **Selected Approach:** Option 3 — Separate Compose deployment preserving all Postiz dependencies until verified otherwise.
- **Reason:** Sharing databases violates service ownership boundaries and risks Postiz migration failures; stripping Temporal/ES without proving compatibility breaks Postiz's `apps/orchestrator` workflows.
- **Consequences:** Higher baseline Docker RAM consumption (~9 Postiz containers), making `docker stats` profiling mandatory in Phase 2 on our `16 GB` laptop.
- **Status:** `ACCEPTED`

---

## ADR-003: Next.js + TypeScript Dashboard & Python + FastAPI Backend
- **Decision ID:** ADR-003
- **Decision Title:** Use Next.js (TypeScript) Frontend and Python 3.12 + FastAPI Backend
- **Date:** 2026-11-10
- **Context:** The application combines an interactive approval/calendar web UI with heavy Python-centric media processing (`Pillow`, `Playwright`, `FFmpeg` orchestration), AI SDKs (`google-genai`), and data pipelines.
- **Options Considered:**
  1. Full TypeScript monorepo (Next.js + NestJS).
  2. Python monolithic server-rendered UI (like SocialAI's `http.server` in `scripts/dashboard.py`).
  3. Next.js + TypeScript dashboard paired with Python 3.12 + FastAPI backend.
- **Selected Approach:** Option 3 — Next.js + TypeScript (`apps/web`) and Python 3.12 + FastAPI (`apps/api`).
- **Reason:** Python dominates AI/media tooling while Next.js + TypeScript provides a modern, strongly typed dashboard without exposing backend secrets to browser JavaScript.
- **Consequences:** Dual-language CI and type checks (`mypy` + `tsc`); clean OpenAPI schema boundary between `apps/api` and `apps/web`.
- **Status:** `ACCEPTED`

---

## ADR-004: PostgreSQL as Primary Application Data Store
- **Decision ID:** ADR-004
- **Decision Title:** Adopt PostgreSQL Instead of SocialAI's SQLite (`brain.db`)
- **Date:** 2026-11-10
- **Context:** SocialAI uses a local SQLite file (`scripts/db.py` → `data/brain.db`) with ad-hoc `ALTER TABLE` migrations. Media Intelligence OS runs API, scheduler, research worker, and creative worker concurrently in Docker.
- **Options Considered:**
  1. Reuse SQLite (`brain.db`) from SocialAI.
  2. Use a dedicated PostgreSQL instance for Media Intelligence OS (separate from Postiz's PostgreSQL).
- **Selected Approach:** Option 2 — Dedicated application PostgreSQL database (deployed in Phase 3).
- **Reason:** SQLite suffers write-lock contention under concurrent Celery workers and API requests and lacks robust transactional migrations/JSON indexing.
- **Consequences:** Requires proper Alembic migrations, container health checks, and automated `pg_dump` backups; eliminates SQLite locking bugs.
- **Status:** `ACCEPTED`

---

## ADR-005: Redis + Celery for Background Processing & Scheduling
- **Decision ID:** ADR-005
- **Decision Title:** Use Redis and Celery Workers (`workers/research`, `workers/creative`)
- **Date:** 2026-11-10
- **Context:** Discovery, verification, carousel rendering, and FFmpeg video assembly are long-running I/O and CPU tasks that cannot block HTTP API requests or rely on an in-process thread like SocialAI's `scheduler.py`.
- **Options Considered:**
  1. In-process Python threads inside the web server (SocialAI pattern).
  2. Dedicated Redis broker + Celery workers (`research`, `creative` with `concurrency=1`) + Celery Beat.
- **Selected Approach:** Option 2 — Dedicated Redis + Celery workers (deployed in Phase 3+).
- **Reason:** Provides isolated worker queues, configurable concurrency caps (`concurrency=1` for creative jobs on 16 GB laptop), retries, and durable task state independent of HTTP restarts.
- **Consequences:** Slightly more Compose services than an in-process thread, requiring memory discipline and clean shutdown handling.
- **Status:** `ACCEPTED`

---

## ADR-006: Remote Gemini Developer API as AI Inference & TTS Layer
- **Decision ID:** ADR-006
- **Decision Title:** Centralize LLM and TTS Inference on Remote Gemini Developer API
- **Date:** 2026-11-10
- **Context:** Target hardware (`i5-13420H`, `16 GB DDR5`, integrated Intel graphics) cannot sustainably run local LLMs alongside Docker + Postiz + FFmpeg + Playwright without severe memory/thermal degradation.
- **Options Considered:**
  1. Local Ollama inference (SocialAI pattern).
  2. Remote Gemini Developer API + Gemini TTS behind a centralized provider adapter (`apps/api` / shared service).
- **Selected Approach:** Option 2 — Centralized Gemini Developer API adapter (Phase 7), with strict single-account quota tracking, hard daily budget stops, circuit breaker, and **zero** multi-account 429-evasion rotation.
- **Reason:** Preserves laptop RAM/CPU for Docker and FFmpeg while enabling high-quality structured JSON generation and TTS.
- **Consequences:** Requires internet access and valid API quota when Phase 7 runs; model IDs and schemas must be verified live at Phase 7 start rather than hardcoded today.
- **Status:** `ACCEPTED` (Adapter policy locked; actual model IDs/quotas `OWNER_CONFIRMATION_REQUIRED` in Phase 7)

---

## ADR-007: Local FFmpeg-Based Video Composition & HTML/CSS/SVG/Pillow/Playwright Carousel Rendering
- **Decision ID:** ADR-007
- **Decision Title:** Deterministic Local Rendering for Carousels and Vertical Shorts
- **Date:** 2026-11-10
- **Context:** We need crisp, brand-consistent Instagram carousels and `9:16` narrated Shorts without paying per-render SaaS video API fees.
- **Options Considered:**
  1. Paid cloud video/design SaaS APIs.
  2. Unconstrained AI-generated image slides only (SocialAI / Pollinations pattern).
  3. Local HTML/CSS/SVG + Playwright/Pillow for carousels and local FFmpeg for Short video/audio assembly (at `concurrency=1`).
- **Selected Approach:** Option 3 — Local deterministic rendering stack (`FFmpeg` + `HTML/CSS/SVG/Pillow/Playwright`).
- **Reason:** Zero per-render monetary cost, exact typographic legibility, deterministic visual QA checks, and reusable visual lessons from SocialAI's FFmpeg filtergraphs.
- **Consequences:** CPU-intensive rendering requires `concurrency=1` and disk cleanup policies; host/container images must include FFmpeg and headless Chromium only in the creative worker.
- **Status:** `ACCEPTED`

---

## ADR-008: API / MCP Boundary for Publishing via Postiz
- **Decision ID:** ADR-008
- **Decision Title:** Communicate with Postiz Exclusively via Public REST API or MCP
- **Date:** 2026-11-10
- **Context:** Postiz (`v1.47.0`) exposes `/public/v1/*` REST routes (`PublicIntegrationsController`) authenticated via API key and an MCP server (`start.mcp.ts`). SocialAI's `meta.py` lacks Instagram publishing.
- **Options Considered:**
  1. Build direct Meta Graph API + YouTube Data API OAuth integrations inside `media-intelligence-os`.
  2. Call Postiz over `/api/public/v1` (and/or Postiz MCP) as the sole publishing boundary.
- **Selected Approach:** Option 2 — Postiz Public API / MCP boundary (implemented in Phase 8 after Phase 2 validates Postiz).
- **Reason:** Eliminates duplicating complex OAuth token management, Meta/YouTube error handling, and scheduling workflows while preserving clean separation.
- **Consequences:** Instagram production publishing still requires publicly reachable HTTPS media URLs (e.g., future owner-approved Cloudflare R2 or equivalent); localhost URLs cannot be fetched by Meta Graph API.
- **Status:** `ACCEPTED`

---

## ADR-009: Mandatory Human Approval Gates (Instagram Drafts & YouTube Explicit Approval)
- **Decision ID:** ADR-009
- **Decision Title:** Enforce Human-in-the-Loop Publishing Safety Gates
- **Date:** 2026-11-10
- **Context:** Autonomous LLM + media pipelines can hallucinate, produce rendering defects, or trigger platform strikes if allowed to auto-publish unchecked (SocialAI ships `approval_mode: "auto"` in `rules.json`, which is unsafe for production branding).
- **Options Considered:**
  1. Immediate auto-publishing after automated QA.
  2. Draft/review mode for Instagram initially, and mandatory explicit human approval before every YouTube upload or approved batch (exact video, channel, metadata, visibility).
- **Selected Approach:** Option 2 — Enforce explicit human approval gates in backend state transitions and UI/MCP policies.
- **Reason:** Protects account reputation, YouTube API quota, and content quality during initial rollout.
- **Consequences:** Owner must review and approve items in the dashboard before live YouTube upload; automated workers stop safely at `pending_approval` / Postiz draft.
- **Status:** `ACCEPTED` (Initial cadence & exact transition to trusted IG scheduling: `OWNER_CONFIRMATION_REQUIRED`)

---

## ADR-010: Local-First Docker Compose Runtime Independent of Antigravity
- **Decision ID:** ADR-010
- **Decision Title:** Run All Production Services via Docker Compose on Local Workstation
- **Date:** 2026-11-10
- **Context:** Antigravity is the coding/development environment, not an always-on production orchestrator.
- **Options Considered:**
  1. Run services manually via host shell scripts (`run_dashboard.bat`) or Antigravity tasks.
  2. Package all custom application services (`apps/web`, `apps/api`, `workers/*`, `services/mcp`, DB, Redis) into a standalone Docker Compose stack separate from Postiz.
- **Selected Approach:** Option 2 — Docker Compose runtime bound to `127.0.0.1`, starting in Phase 1 with a minimal `/health` baseline (`infra/docker/compose.baseline.yml`).
- **Reason:** Reproducible builds, isolated dependency versions (Python 3.12 vs host Python 3.14), clean health checks, and one-command startup/shutdown independent of the IDE.
- **Consequences:** Requires Docker Desktop running; subject to laptop sleep/power limitations documented in `docs/OPERATIONS.md`.
- **Status:** `ACCEPTED`

---

## ADR-011: Pin Python 3.12 Runtime Despite Host Python 3.14 Default
- **Decision ID:** ADR-011
- **Decision Title:** Standardize Local `.venv` and Container Runtime on Python 3.12
- **Date:** 2026-11-10
- **Context:** Environment inspection showed host `python` defaults to `3.14.6`, while Python `3.12.9` is also installed via `py -3.12`. Many data, Playwright, Celery, and native wheels lack stable `3.14` builds.
- **Options Considered:**
  1. Target host default Python `3.14.6`.
  2. Pin project `.venv` and Docker images to Python `3.12` (`python:3.12-slim`).
- **Selected Approach:** Option 2 — Python `3.12` baseline.
- **Reason:** Ensures binary wheel availability and parity between Windows `.venv` and Linux Docker containers.
- **Consequences:** Developers must invoke `uv venv --python 3.12` or `py -3.12` instead of bare `python` when creating virtual environments.
- **Status:** `PROPOSED` (`OWNER_CONFIRMATION_REQUIRED` only if owner prefers 3.13; currently verified working on 3.12.9)

---

## ADR-012: Source Registry Policy & Reddit Blocked Status
- **Decision ID:** ADR-012
- **Decision Title:** Enforce Explicit Source Lifecycle States and Block Reddit Pending Legal/ToS Review
- **Date:** 2026-11-10
- **Context:** SocialAI scrapes Reddit RSS/JSON endpoints directly (`scripts/research.py`), which triggers HTTP 403/429 blocks and raises Data API Terms compliance questions.
- **Options Considered:**
  1. Enable Reddit scraping/RSS out of the box.
  2. Restrict connectors to official APIs / RSS / permitted low-frequency official monitoring with explicit status flags (`OFF`, `EXPERIMENTAL`, `ENABLED`, `BLOCKED`), keeping Reddit `BLOCKED` and Google Trends `EXPERIMENTAL`.
- **Selected Approach:** Option 2 — Explicit Source Registry with Reddit `BLOCKED`.
- **Reason:** Prevents ToS violations, brittle scraping, and noisy low-signal topics; protects against prompt injection via strict untrusted-input boundaries.
- **Consequences:** Reddit topics will not be ingested unless/until the owner completes a permitted-use review and explicitly approves enabling it.
- **Status:** `ACCEPTED` (Policy locked; specific source activation in Phase 5 requires `OWNER_CONFIRMATION_REQUIRED`)

---

## ADR-013: Provisional Content Direction & Future Public Media Hosting (Cloudflare R2)
- **Decision ID:** ADR-013
- **Decision Title:** Treat Niche, Cadence, and Cloudflare R2 Media Hosting as Provisional Pending Owner Approval
- **Date:** 2026-11-10
- **Context:** Initial content niche (AI & developer intelligence, English, 1 IG carousel/day, 2–3 YT Shorts/week) and future Cloudflare R2 usage (required for public HTTPS Instagram media URLs under zero-paid-budget constraints) are not yet formally signed off by the owner.
- **Options Considered:**
  1. Lock provisional content direction and configure Cloudflare R2 immediately.
  2. Record them as provisional (`OWNER_CONFIRMATION_REQUIRED`), keep `.env.example` placeholder-only (`CLOUDFLARE_R2_ENABLED=false`), and require explicit owner approval before spending money or provisioning R2.
- **Selected Approach:** Option 2 — Keep all unapproved content defaults and R2 configuration explicitly marked `OWNER_CONFIRMATION_REQUIRED`.
- **Reason:** Respects Phase 0/1 scope control and strict `$0` unauthorized spend policy.
- **Consequences:** Phase 5/7/8 prompts and scheduling defaults will read from owner-confirmed configuration once approved.
- **Status:** `PROPOSED` (`OWNER_CONFIRMATION_REQUIRED`)

---

## ADR-014: Multi-Brand and Multi-Channel Support
- **Decision ID:** ADR-014
- **Decision Title:** Support Multiple Independent Brand Profiles and Connected Channel Accounts with Shared Research Intelligence
- **Date:** 2026-11-10
- **Context:** The initial Phase 0 PRD assumed a single personal brand/account. The product actually targets multiple independent brand identities—starting with **Brand Profile 1 (Technology and AI)** and **Brand Profile 2 (App Promotion)**—mapped to separate Instagram Professional accounts (and future optional YouTube channels).
- **Options Considered:**
  1. Keep a single-tenant/single-brand schema and deploy separate application stacks per brand.
  2. Model `BrandProfile` and `ChannelAccount` explicitly inside Media Intelligence OS, sharing research discovery/verification while isolating editorial selection, prompts, visual templates, tone, CTAs, schedules, calendars, analytics, and approval policies per brand/account.
- **Selected Approach:** Option 2 — Multi-brand and multi-channel architecture with strict `(brand_id, channel_account_id)` attribution on every `ContentPackage`, `Asset`, `Schedule`, and `Post`, plus account-level publish guards preventing cross-account mis-posting.
- **Reason:** Maximizes reuse of shared primary-source research while enforcing brand differentiation and eliminating single-account lock-in without running redundant Docker stacks on a `16 GB` laptop.
- **Consequences:** Phase 3 PostgreSQL schema and Phase 6–10 workflows must enforce brand/channel scoping; **no database migrations or schema code are created during Phase 2**.
- **Status:** `ACCEPTED` (Product & architecture requirement; Phase 3+ implementation work deferred)

---

## ADR-015: Pin Official Postiz v2.25.3 Deployment in Separate Runtime Directory
- **Decision ID:** ADR-015
- **Decision Title:** Deploy Pinned Official Postiz `v2.25.3` Image via `postiz-docker-compose` Topology in `C:\Media OS Agent\postiz-runtime`
- **Date:** 2026-11-10
- **Context:** The local reference snapshot `postiz-app-main` reports `v1.47.0` in `version.txt` and must remain untouched. Upstream `gitroomhq/postiz-app` release `v2.25.3` (2026-10-24) patches security advisories `PSA-2026-E9AHFV` and `PSA-2026-YT2HMH`, and `gitroomhq/postiz-docker-compose` defines the current official multi-container healthcheck and persistent volume topology.
- **Options Considered:**
  1. Deploy from the extracted `v1.47.0` archive or mutable `:latest` Docker image tag.
  2. Deploy into `C:\Media OS Agent\postiz-runtime` using `ghcr.io/gitroomhq/postiz-app:v2.25.3` pinned by OCI digest alongside pinned Postgres, Redis, Elasticsearch, and Temporal images from `gitroomhq/postiz-docker-compose`.
- **Selected Approach:** Option 2 — Immutable version + digest pinning in isolated `postiz-runtime` directory, exposing only `127.0.0.1:4007:5000` to the host.
- **Reason:** Eliminates known security vulnerabilities, avoids mutable `:latest` drift, preserves `postiz-app-main` untouched, and keeps internal Postgres/Redis/Elasticsearch/Temporal ports off the Windows host.
- **Consequences:** Requires verifying container memory consumption against the `~7.61 GiB` Docker Engine budget in `docs/POSTIZ_RESOURCE_PROFILE.md`.
- **Status:** `ACCEPTED`

---

## ADR-016: Verified Postiz v2.25.3 Public API & MCP Interface Contract
- **Decision ID:** ADR-016
- **Decision Title:** Enforce Verified Route Paths, Header Formats, and Draft Prerequisites Against Postiz `v2.25.3`
- **Date:** 2026-11-10
- **Context:** Phase 2 live verification revealed key interface details in `v2.25.3` that differ from casual assumptions: (1) nginx proxies `/api/` to NestJS `3000` and `/` to Next.js `4200`, so `/mcp` redirects to `/auth` while `/api/mcp` serves Streamable HTTP MCP; (2) `PublicAuthMiddleware` on `/api/public/v1/*` expects raw `Authorization: <key>` (rejecting `Bearer`), whereas `start.mcp.ts` on `/api/mcp` accepts `Authorization: Bearer <key>`; (3) `POST /api/public/v1/posts` (`type: "draft"`) requires at least one connected `integration.id`; (4) `DISABLE_REGISTRATION=true` blocks local signup and OIDC/OAuth sign-in while preserving existing `LOCAL` owner login; (5) after `postiz` restarts, nginx/Next.js `5000` becomes healthy ~2s before NestJS `3000`, so readiness polling must check `/api/auth/can-register`.
- **Options Considered:**
  1. Assume uniform `Bearer` auth and `/mcp` root routing, or modify Postiz nginx/auth middleware.
  2. Adapt our future Phase 8 Postiz client and operational runbooks to Postiz's verified `v2.25.3` contract without modifying Postiz internals.
- **Selected Approach:** Option 2 — Preserve unmodified upstream `v2.25.3` behavior and encode exact verified contracts in tests and docs.
- **Reason:** Maintains clean AGPL/service separation and guarantees zero divergence from pinned upstream container images.
- **Consequences:** Phase 8 Postiz client must use raw `Authorization: <key>` for `/api/public/v1`, `Authorization: Bearer <key>` for `/api/mcp`, wait for `/api/auth/can-register` on restarts, and only create Postiz drafts after a channel integration exists.
- **Status:** `ACCEPTED`

---

## ADR-017: Phase 3 Non-DB Application Skeleton, Bootstrap Auth & BFF Proxy

- **Decision ID:** ADR-017
- **Decision Title:** Build Next.js + FastAPI Application Foundation Ahead of Phase 4 Persistence Using Fail-Closed Bootstrap Auth and Same-Origin BFF Proxy
- **Date:** 2026-11-10
- **Context:** Earlier roadmap drafts placed Next.js UI in Phase 10 and PostgreSQL/Redis in Phase 3. Phase 3 authorization establishes the full dark-first 12-route Next.js dashboard and FastAPI control plane first (without PostgreSQL, Redis, or Celery) while preserving the Phase 1 stdlib `/health` baseline (`media_os_api.health`) and protecting all routes behind genuine local authentication.
- **Options Considered:**
  1. Expose browser `fetch()` directly from `localhost:3000` to `127.0.0.1:8000` with client-readable bearer tokens or `localStorage`, or skip auth until PostgreSQL exists.
  2. Implement a fail-closed single-owner Argon2id bootstrap (`OWNER_PASSWORD_HASH_B64` avoiding Docker Compose `$` interpolation) with HMAC-SHA256 `HttpOnly` + `SameSite=Lax` cookies, double-submit CSRF tokens, bounded JTI revocation, login rate limiting, and a server-side Next.js `/api/v1/[...path]` BFF proxy forwarding to `INTERNAL_API_BASE_URL` (`http://media-os-api:8000`).
- **Selected Approach:** Option 2 — Same-origin Next.js BFF proxy + fail-closed single-owner cookie/CSRF auth + explicitly labeled non-persistent `phase3_prototype_fixture` brand/channel models (`Brand 1: Technology & AI`, `Brand 2: App Promotion`).
- **Reason:** Eliminates `localhost` vs `127.0.0.1` cross-origin cookie issues, keeps zero secrets in `NEXT_PUBLIC_*` or `localStorage`, validates end-to-end UI/API security early, and limits combined container RAM overhead to `< 100 MiB`.
- **Consequences:** `GET /health/ready` and `/api/v1/system/status` explicitly report `database` and `redis` as `not_implemented` (`phase-4`). Database schema migrations and persistent users/brands arrive cleanly in Phase 4.
- **Status:** `ACCEPTED`

---

## ADR-018: Synchronous SQLAlchemy 2.x + `psycopg[binary]` Transaction Strategy
- **Decision ID:** ADR-018
- **Decision Title:** Standardize on Synchronous SQLAlchemy 2.x + `psycopg` Across API, Alembic, Celery Workers, and Scheduler
- **Date:** 2026-11-10
- **Context:** Media Intelligence OS runs FastAPI HTTP handlers alongside Alembic migrations, synchronous Celery worker tasks (`--concurrency=1`), a polling outbox/scheduler daemon, and standalone operational CLIs. Mixing asyncpg/async SQLAlchemy sessions with synchronous Celery/Alembic paths risks event-loop mismatches, detached-session bugs, and duplicated engine pools under a strict 512 MiB PostgreSQL cap (`max_connections=40`).
- **Options Considered:**
  1. Async SQLAlchemy (`asyncpg`) inside FastAPI plus a second synchronous `psycopg` engine for Celery/Alembic.
  2. Unified synchronous SQLAlchemy 2.x + `psycopg[binary]` (`Session` + explicit `session_scope()` transaction boundaries, executed safely in FastAPI sync route workers or `anyio.to_thread.run_sync`).
- **Selected Approach:** Option 2 — Single synchronous SQLAlchemy 2.x + `psycopg[binary]` stack (`pool_size=5`, `max_overflow=5`, `pool_pre_ping=True`, `connect_timeout=5`).
- **Reason:** Guarantees consistent locking (`SELECT ... FOR UPDATE SKIP LOCKED`), identical behavior across FastAPI, Celery, Alembic, and CLI scripts, and tight connection budgeting against PostgreSQL.
- **Consequences:** All DB endpoints use bounded sync worker execution; future async FastAPI paths requiring Postiz HTTP calls run `httpx.AsyncClient` without holding an open DB session across network I/O.
- **Status:** `ACCEPTED`

---

## ADR-019: Database-Enforced Multi-Brand Isolation via Composite Foreign Keys & PostgreSQL Triggers
- **Decision ID:** ADR-019
- **Decision Title:** Enforce Brand & Claim Integrity Inside PostgreSQL Alongside Service-Layer Scoping
- **Date:** 2026-11-10
- **Context:** Multi-brand isolation (`brand-tech-ai` vs `brand-app-promo`) and claim grounding must hold even if future scripts, migrations, or worker jobs bypass API service helpers.
- **Options Considered:**
  1. Enforce `brand_profile_id` matching purely in Python service code.
  2. Combine service-layer `BrandIsolationError` checks with composite unique keys `(id, brand_profile_id)`, composite foreign keys across `content_packages`, `assets`, `publish_schedules`, `metrics_snapshots`, and PostgreSQL triggers for claim verification, brand immutability, and same-brand logo ownership.
- **Selected Approach:** Option 2 — Defense-in-depth enforcement in both Python services and PostgreSQL DDL/triggers.
- **Reason:** Prevents accidental cross-brand scheduling/asset attachment or unverified `Claim` promotion at the storage layer itself.
- **Consequences:** Schema migrations carry explicit triggers and composite FK definitions; integration tests validate raw `IntegrityError` rejections as well as service exceptions.
- **Status:** `ACCEPTED`

---

## ADR-020: Transactional Outbox (`FOR UPDATE SKIP LOCKED`) + JSON-Only Celery + Persistent Scheduler
- **Decision ID:** ADR-020
- **Decision Title:** Bridge PostgreSQL State and Redis/Celery Execution via a Crash-Safe Transactional Outbox and DST-Aware Scheduler
- **Date:** 2026-11-10
- **Context:** Enqueueing directly to Redis/Celery before or after DB commit creates dual-write race conditions (lost tasks or orphaned tasks if one side crashes). Laptop sleep/hibernation also risks thundering-herd burst execution on wake.
- **Options Considered:**
  1. Direct `.delay()` calls inside API/scheduler code plus stock Celery Beat file-based schedule (`celerybeat-schedule`).
  2. Atomic PostgreSQL `JobRun` + `OutboxEvent` creation, `FOR UPDATE SKIP LOCKED` dispatcher (`media-os-scheduler`), `JobRun` lease locking with stale-lease reaper, JSON-only Celery serialization (`concurrency=1`), and IANA `ZoneInfo` persistent `ScheduleDefinition` advancement skipping past elapsed sleep windows (`anti-burst`).
- **Selected Approach:** Option 2 — Transactional outbox + persistent DB scheduler + lease-guarded idempotent job execution.
- **Reason:** Eliminates dual-write state divergence, avoids Celery Beat local mutable state files on `read_only` containers, and safely prevents burst replay after laptop sleep.
- **Consequences:** Adds a lightweight `media-os-scheduler` container (`~56–65 MiB` RSS) alongside `media-os-worker` (`~88–110 MiB` RSS) while remaining well under the 1.5 GiB custom-stack ceiling.
- **Status:** `ACCEPTED`

---

## ADR-021: Explicit Redis Rate-Limiter Outage Policy
- **Decision ID:** ADR-021
- **Decision Title:** Configure Explicit Fallback vs. Fail-Closed Login Rate Limiting During Redis Outages
- **Date:** 2026-11-10
- **Context:** Phase 4 replaces Phase 3's in-memory login rate limiter with a Redis sorted-set sliding-window pipeline (`RedisLoginRateLimiter` keyed at `media_os:rl:login:*` using `ZREMRANGEBYSCORE` / `ZCARD` / `ZRANGE` and `ZADD` / `EXPIRE`) so brute-force lockouts survive API restarts. A local workstation restart or transient Redis interruption must behave predictably without silently dropping lockout protection.
- **Options Considered:**
  1. Silently reset/allow all login attempts on Redis errors.
  2. Expose `RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE` (`false` local workstation default → bounded in-memory mirrored fallback with structured degraded warning; `true` → raise `RateLimiterUnavailableError` / HTTP `503`).
- **Selected Approach:** Option 2 — Configurable explicit outage behavior defaulting to bounded in-memory mirrored fallback locally and documented fail-closed option.
- **Reason:** Preserves availability for the sole workstation owner during transient Redis restarts while still enforcing active lockout protection and failing closed when configured.
- **Consequences:** Single-instance local deployments remain brute-force protected during brief Redis restarts; production/hardened profiles can flip `RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE=true`.
- **Status:** `ACCEPTED`

---

## ADR-022: Logical `pg_dump -Fc` + Isolated Temporary-DB Restore Verification Outside Git
- **Decision ID:** ADR-022
- **Decision Title:** Execute Host-Side Backup & Isolated Temporary-Database Restore Verification Against `media-os-postgres`
- **Date:** 2026-11-10
- **Context:** Keeping PostgreSQL (`5432`) unexposed to the Windows host LAN improves security while still requiring repeatable backups (`pg_dump`) and genuine restoration tests that never endanger live `media_os` data or leak `.dump` files into Git.
- **Options Considered:**
  1. Publish PostgreSQL `127.0.0.1:5432` to the host or copy raw Docker volume files while running.
  2. Keep `5432` internal to `media-os-net` and run `scripts/backup_db.py` (`docker exec` `pg_dump -Fc` + SHA-256 + TOC check) and `scripts/restore_verify.py` (restoring into disposable `media_os_restore_verify_*` DB inside `media-os-postgres`, comparing checksums/row counts, then dropping the temp DB) storing artifacts in `C:\Media OS Agent\media-os-backups\`.
- **Selected Approach:** Option 2 — Isolated `pg_dump -Fc` + temporary-DB restore verification outside Git.
- **Reason:** Zero host DB port exposure, consistent transactional snapshots without stopping the DB, and automated end-to-end backup integrity verification.
- **Consequences:** Requires `docker exec` access for host operators and storing `.dump`/`.sha256` files in an external host folder outside the repository.
- **Status:** `ACCEPTED`

---

## ADR-023: Cross-Platform Pre-Commit Tooling Parity via Python-Standard-Library Dispatcher
- **Decision ID:** ADR-023
- **Decision Title:** Route Pre-Commit Python Tool Invocations Through Standard-Library Dispatcher (`scripts/run_in_env.py`)
- **Date:** 2026-11-11
- **Context:** Developers and Git commit hooks on Windows environments frequently experienced hook failures (`ruff` or `mypy` not found) because tool binaries were installed exclusively inside the project's Python 3.12 `.venv` and not exposed on the system global `PATH`. This led to manual bypasses (`git commit --no-verify`), breaking local quality-gate parity with CI.
- **Options Considered:**
  1. Isolated pre-commit virtual environments (`repo: https://github.com/...`). Rejected because MyPy requires access to installed project site-packages and dependency types (`fastapi`, `sqlalchemy`, `pydantic`), resulting in 160+ false-positive type check failures when isolated.
  2. Mandate Windows global system `PATH` modifications for every developer workstation. Rejected as fragile, machine-dependent, and prone to version drift.
  3. Implement a standard-library-only executable dispatcher (`scripts/run_in_env.py`) that strictly resolves executables from `.venv/Scripts` (Windows) or `.venv/bin` (Unix), falling back to CI PATH when `CI=true`.
- **Selected Approach:** Option 3 — Standard-library dispatcher in `.pre-commit-config.yaml`.
- **Reason:** Guarantees deterministic, reproducible tool execution under Windows PowerShell, bash, IDE Git clients, and GitHub Actions CI without requiring global tool installation or `--no-verify` workarounds.
- **Consequences:** All developers must maintain an active project `.venv` (`uv venv .venv --python 3.12 && uv pip install -e ".[dev]"`). Pre-commit hooks execute reliably across platforms.
- **Status:** `ACCEPTED`

---

## ADR-024: Multi-Brand Shared Research Decoupling via `BrandTopicAssignment` Bridge Entity
- **Decision ID:** ADR-024
- **Decision Title:** Decouple Globally Shared Ingestion from Brand-Specific Editorial Screening Using `BrandTopicAssignment`
- **Date:** 2026-11-11
- **Context:** In Phase 4, `source_items` and `topic_clusters` were designed as global, shared research assets to prevent redundant fetching, network hammering, and duplicate scraping across brands. However, `research_packets` and downstream content require strict brand isolation. Directly scoping `topic_clusters` to `brand_profile_id` would destroy global deduplication and multiply crawler load.
- **Options Considered:**
  1. Add `brand_profile_id` directly to `source_items` and `topic_clusters`. Rejected because it duplicates ingestion pipelines per brand and creates data synchronization nightmares.
  2. Maintain globally shared `source_items` and `topic_clusters`, while introducing `BrandTopicAssignment` (`brand_topic_assignments`) as an explicit bridge entity storing per-brand editorial status, priority, and relevance scores. Enforce integrity via composite foreign key `fk_research_packets_brand_topic (brand_profile_id, topic_cluster_id)` and service-layer eligibility guards.
- **Selected Approach:** Option 2 — Explicit `BrandTopicAssignment` bridge table with composite foreign keys.
- **Reason:** Preserves global content discovery and deduplication while providing total brand isolation for editorial decisions, scoring, and research dossier scoping.
- **Consequences:** Migration `0004_add_brand_topic_assignments` creates `brand_topic_assignments` table with lock timeouts (`5s`) and adds composite FK on `research_packets`. Creating a `ResearchPacket` requires an existing assignment in `APPROVED_FOR_RESEARCH` or `SHORTLISTED` status.
- **Status:** `ACCEPTED`
