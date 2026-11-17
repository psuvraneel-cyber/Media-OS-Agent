# Operations & Reliability Guide — Media Intelligence OS

**Document Status:** Phase 4–5 Operations & Reliability Runbook
**Last Updated:** 2026-11-14

---

## 1. Expected Runtime Topology

Media Intelligence OS is designed as two **independent** local Docker Compose deployments running on Docker Desktop (WSL2 Linux engine):

1. **Stack A — `media-os-baseline` (Phase 1) → `media-intelligence-os` (Phase 4 Active Stack):**
   - **Phase 1 Active Baseline:** Single minimal health service (`infra/docker/compose.baseline.yml`) bound to `127.0.0.1:8000` on isolated network `media-os-baseline-net`.
   - **Phase 4 Persistence & Durable Jobs Stack (`infra/docker/compose.app.yml`):** 6 containers on isolated `media-os-net`: `media-os-web` (`127.0.0.1:3100`; port set by `MEDIA_OS_WEB_PORT`, login origins by `MEDIA_OS_WEB_ORIGINS` — 3000 is used by another local project), `media-os-api` (`127.0.0.1:8000`), `media-os-postgres` (PostgreSQL 17, `5432` internal), `media-os-redis` (Redis 7.2, `6379` internal), `media-os-worker` (Celery `--concurrency=1`), and `media-os-scheduler` (outbox dispatcher + persistent scheduler + lease reaper). *(Future Phase 5/7 `research` & `creative` workers will bind to reserved queues.)*
2. **Stack B — `postiz-runtime` (Phase 2 Verified, separate directory `C:\Media OS Agent\postiz-runtime`):**
   - Deployed independently using pinned release `v2.25.3` (`ghcr.io/gitroomhq/postiz-app:v2.25.3`), exposing `http://localhost:4007` (`127.0.0.1:4007:5000`), with 8 running containers and 6 persistent named volumes. See [`docs/POSTIZ_DEPLOYMENT.md`](./POSTIZ_DEPLOYMENT.md).

> [!IMPORTANT]
> Antigravity is **not** required at runtime. Once containers are started via Docker Compose, closing Antigravity does not stop the application.

---

## 2. Docker Startup & Operational Runbooks

### Phase 4 Persistent Stack Commands (`media-intelligence-os`)

Run from `C:\Media OS Agent\media-intelligence-os` using the external ACL-locked runtime `.env` (`C:\Media OS Agent\media-os-runtime\.env`):

```powershell
# Start database and Redis first or full stack (fail-closed if POSTGRES_PASSWORD / REDIS_PASSWORD missing)
docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml up -d --build --wait

# Run Alembic schema migrations and seed idempotent brands from inside media-os-net
docker exec media-os-api alembic upgrade head

# Deliberate single-owner password rotation (outside Git env; revokes all active sessions)
docker exec -it media-os-api python -m media_os_api.auth.rotate_password --username <owner_username>

# Create host-side PostgreSQL backup + verify restore inside an isolated temporary database
.venv\Scripts\python.exe scripts/backup_db.py
.venv\Scripts\python.exe scripts/restore_verify.py

# Inspect status, health endpoints, and worker heartbeats
docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml ps
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready"
Invoke-RestMethod -Uri "http://127.0.0.1:3100/api/health"

# Safe stop (NEVER `-v`! Stop custom app before running `pytest tests/smoke` because Phase 1 also binds 127.0.0.1:8000)
docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml down
```

### Pre-Commit Quality Gates & Tool Dispatcher
All Git commits automatically execute the project's pre-commit suite without requiring tools to be in system PATH:
```powershell
# Run all pre-commit hooks manually across the entire repository
.\media-intelligence-os\.venv\Scripts\pre-commit.exe run --all-files

# Or using the cross-platform dispatcher directly
python scripts/run_in_env.py ruff format --check
python scripts/run_in_env.py ruff check
python scripts/run_in_env.py mypy
python scripts/run_in_env.py python scripts/scan_secrets.py
python scripts/run_in_env.py python scripts/generate_ref_manifests.py --verify
```
> [!NOTE]
> Bypassing Git hooks with `--no-verify` is strictly prohibited. If a check fails, resolve the violation or run `python scripts/run_in_env.py ruff format` to auto-format before committing.

### Migration Safety & Destructive Downgrade Policy (Migration 0004)
Migration `0004_add_brand_topic_assignments` establishes the multi-brand editorial bridge (`brand_topic_assignments`).
> [!CAUTION]
> **Destructive Downgrade Policy:** Once `BrandTopicAssignment` records are created, downgrading migration `0004` (`alembic downgrade 0003_domain_integrity_hardening`) drops the `brand_topic_assignments` table and permanently destroys all brand-specific editorial statuses, relevance scores, and reviewer notes.
>
> Downgrading migration `0004` is **strictly prohibited** unless all three mandatory conditions are met:
> 1. **Explicit Owner Authorization:** Written owner confirmation authorizing data loss.
> 2. **Verified Database Backup:** Full PostgreSQL dump created via `python scripts/backup_db.py`.
> 3. **Verified Restoration Procedure:** Recovery verified inside an isolated database via `python scripts/restore_verify.py`.

### Phase 5 Research Ingestion Runbook & Invariants
- **Scheduled fetches:** `media_os.research.fetch.<source>` jobs on the `research` queue (Celery task `media_os.execute_research_job_run`, lease 900 s). Five global schedules exist, **disabled** by default.
- **Owner admin CLI:** `docker exec media-os-api python -m media_os_api.research.admin status|set-mode|approve-live|revoke-live|schedule|run-now` (every change audited; Reddit refused).
- **Live-fetch gate:** source mode `EXPERIMENTAL`/`ENABLED` **and** `approve-live` **and** `RESEARCH_LIVE_FETCH_ENABLED=true` in the runtime `.env` (worker). Optional read-only `GITHUB_TOKEN`.
- **Integration tests:** `python scripts/run_integration_tests.py` only (disposable tmpfs stack). The suite refuses the runtime DB/Redis.
- **Owner-gated live smoke:** `python scripts/research_live_smoke.py --source <CODE> --owner-approved` (at most 10 items, disposable DB).
- **Manual ingest task:** `media_os.research.ingest` still accepts sanitised caller-supplied records (payload scrubbed after success).
- **Provider Namespaces:** External resources are strictly partitioned by type (`GITHUB_REPOSITORY`, `GITHUB_RELEASE`, `HUGGINGFACE_MODEL`, `HUGGINGFACE_DATASET`, `HUGGINGFACE_SPACE`, `HACKERNEWS_STORY`, `ARXIV_PAPER`, `OPENAI_NEWS`).
- **Policy Enforcement:** Sources start `OFF` in `research_sources`; stored items are marked `EXPERIMENTAL`. The Reddit connector is strictly `BLOCKED` (`PolicyBlockedError`).
- **Automated Brand Assignment:** Discovery of any new `TopicCluster` automatically generates one `BrandTopicAssignment` for each active brand with `editorial_status="PENDING_EVALUATION"` and `relevance_score=NULL`.
- **Global Cluster Isolation:** Shared fields `TopicCluster.status` and `TopicCluster.score_breakdown` represent global discovery lifecycle only; per-brand decisions reside exclusively in `BrandTopicAssignment`.
- **Minimal Retention & Sanitization:** Ingested payloads are untrusted; summaries are stripped of HTML and truncated to `<= 500` characters (`minimal_permitted_summary`), with `retention_expires_at` populated. Raw source bodies or comments are never persisted.

### Phase 1 Baseline & Phase 2 Standalone Postiz Commands
- **Phase 1 Baseline:** `docker compose -f infra/docker/compose.baseline.yml up -d --build` → `http://127.0.0.1:8000/health` → `down`.
- **Postiz (`C:\Media OS Agent\postiz-runtime`):** `docker compose up -d --wait` → verify `http://localhost:4007/api/auth/can-register` (`{"register":false}`) → `docker compose down` (NEVER `down -v`).

### Strict Safety Rules
- **NEVER** run `docker compose down -v`, `docker volume prune`, `docker system prune`, or `docker container prune` without explicit owner authorization—doing so wipes PostgreSQL databases, Redis queues, and Postiz OAuth tokens.
- Always target specific Compose files (`-f ...`) or project names (`-p ...`) so Media OS commands never touch `postiz-runtime`.

---

## 3. Container Health & Resource Monitoring

- **Health Checks Active:** `media-os-api` (`/health/live`), `media-os-web` (`/api/health`), `media-os-postgres` (`pg_isready`), `media-os-redis` (`redis-cli ping`), `media-os-worker` (`/tmp/media_os_worker.heartbeat` + Redis ping), and `media-os-scheduler` (`/tmp/media_os_scheduler.heartbeat`).
- **Observed Phase 4 Memory Profile (`~7.61 GiB` WSL2 `MemTotal` / `16 GB` Host):**
  - `media-os-postgres`: `22.96–34.76 MiB` / `512m` limit (`shared_buffers=128MB`, `work_mem=4MB`, `max_connections=40`, `TZ=UTC`).
  - `media-os-redis`: `4.45–4.63 MiB` / `128m` limit (`maxmemory 96mb`, `noeviction`, `appendonly yes`).
  - `media-os-api`: `72.46–73.31 MiB` / `256m` limit.
  - `media-os-worker`: `88.31–110 MiB` / `256m` limit (`--concurrency=1`).
  - `media-os-scheduler`: `56.25–64.52 MiB` / `128m` limit.
  - `media-os-web`: `35.28–47.75 MiB` / `256m` limit.
  - **Combined Media OS Footprint:** `~279.9 MiB` steady-state RSS across 6 containers alongside all 8 `postiz-runtime` containers (`~2.3 GiB` total RSS; WSL2 `3.6 GiB used / 3.8 GiB available`).

---

### Studio: the owner's own reels, photos and carousels, brand logo, Sync now

- **Photos and carousels.** Images (PNG, JPEG or WebP, up to 8 MB each) are staged one by one, then become one post: 1 image is a photo post (`POST`), 2–10 a carousel, in the order the owner set. Checks flag shapes Instagram would crop. At publish time images are converted to JPEG with phone rotation applied, and capped at 2160 px. Owner uploads are marked by `qa_report.version` starting `owner-`, so they're reviewed in Studio and kept out of the generated-carousel review queue.
- **Upload → approve → schedule.** Studio takes MP4 reels (up to 95 MB, 3 s–15 min; 9:16 recommended). Each upload becomes a `REEL` content package, checked on arrival (real MP4 with a video track, size, length, aspect ratio). It waits for the owner's approval, using the same decision endpoint as carousels. Captions and hashtags are editable until approval. Approved reels can be scheduled one at a time from Approvals, or in a batch from Studio (first time + spacing). Each item goes through the usual checks: 20 min lead time, 30-day window, daily cap, 5-min spacing. Requests still waiting for the worker count toward the cap and spacing too.
- **Publishing.** The worker commits the video to the GitHub Pages media repo and gives Postiz the `.mp4` URL. Postiz posts a single MP4 as an Instagram Reel and a Facebook video reel. Big files are streamed, never held in memory.
- **Storage.** Uploads live on the `media_os_uploads` volume: written by the API only, read-only for the worker, never by the renderer. Removing a reel deletes its file. Hosted videos stay in the public media repo's history; GitHub recommends keeping a repo under about 1 GB, so roughly 25–30 reels of 30–40 MB.
- **Brand logo.** `PUT /api/v1/brands/{brand}/logo` (PNG, JPEG or WebP, up to 5 MB) is stored as a `BRAND_LOGO` asset and referenced from `visual_identity.logo_asset_id`. It's shown beside the brand name and in the brand switcher.
- **Sync now.** `POST /api/v1/analytics/sync` queues the forced 30-day metrics sync (one job per minute however often it's clicked). The dashboard waits for it to finish, then reloads the numbers.

---

### Owner alerts and self-healing (Telegram)

There are two layers, and both run every 5 minutes.

**In-app (Media OS worker), alerts only:**
- **Queued from the publish status sync:**
  - post **published**: quiet message with the link;
  - post **failed**: Meta and Postiz errors explained in plain words, for example "Meta blocked the app";
    - **automatic retry:** when every platform failed with a temporary error (rate limit, Meta code 1/2/4/17/32/613, media download failed, timeout, 5xx), the package is rescheduled once, 30 min later (40 or 50 min if that slot is taken). The alert says so. Permission, token and policy errors are never retried, and a second failure asks you to reschedule it yourself;
  - post **overdue**.
- **Job that gave up:** any durable job that ends `FAILED_FINAL`.
- **`notify.watchdog`, every 5 min:**
  - a post still queued in Postiz 10 min after its time (its publishing worker is down);
  - Postiz not answering;
  - a disabled Instagram or Facebook channel;
  - the 15-min status sync having stopped.
- **`notify.daily_digest`, 22:00 IST (the nightly report):** what went out, what failed, what's next, the review queue, and the **last 7 days of platform numbers** (views, likes and reactions, comments, shares, saves, reach), compared with the 7 days before, plus the top post.
  - Totals across platforms only add metrics that mean the same everywhere; reach and unique viewers stay per platform.
  - `analytics.sync_post_metrics.nightly` (21:40 IST, `force_recent`) refreshes every post up to 30 days old first, so the numbers are about 20 min old.
  - Facebook comments and shares aren't available through Postiz for photo posts; YouTube appears once a channel is connected.
- **`notify.telegram_commands`, every minute:** reads messages sent to the bot and answers `/status` (worker, scheduler, Postiz, status sync, last and next post, stuck and failed posts, review queue), `/queue` (upcoming posts) and `/help`. It answers only `TELEGRAM_CHAT_ID`, ignores messages older than 10 min, and never changes anything.
- **Whole-account statistics:** `analytics.sync_account_metrics` (daily 21:35 IST, and on Sync now) reads each connected channel's account analytics from Postiz (`GET /analytics/{channel}?date=7|30`) and stores last-7 and last-30 day totals in `account_metric_snapshots`, one row per channel, metric, window and IST day. These cover every post on the account, including ones posted directly in the Instagram or Facebook apps.
  - Instagram gives likes, views, comments, shares, saves and replies as window totals, plus daily reach; follower counts appear only once the account has 100+ followers. Instagram's account numbers can run about 2 days behind post-level numbers.
  - Facebook gives media views, unique viewers, engagements and new follows per day.
  - Sums of daily unique counts (reach, unique viewers) are stored but never shown as reach or added across platforms.
  - Shown in Analytics as "Whole account" and in the 22:00 report as "Whole account · last 7 days".
- **De-duplication:** alerts are durable jobs keyed by the event, so each event is sent once and a Telegram outage only delays them.
- **Configuration:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `MEDIA_OS_DASHBOARD_URL` in the runtime `.env`, worker only. When the token or chat ID is empty, alerts are skipped and nothing else changes.

**Host (`infra/ops/windows/watchdog.ps1`, Task Scheduler), repairs:**
- restarts `postiz-runtime-app` when its worker has no live Temporal pollers for about 10 min, or is crash-looping;
- restarts any watched container that stays unhealthy, and starts any that has stopped;
- restarts the dashboard or API container when it is healthy but Windows can no longer reach its port (Docker lost the port link, which leaves the desktop app on "Media OS isn't reachable yet");
- **watches the watcher:** if Media OS's own `notify.watchdog` hasn't succeeded for 15 min, it tells you directly on Telegram and restarts `media-os-worker` and `media-os-scheduler`;
- **Docker on demand:** when Docker Desktop is off, starts it from 1 hour before a scheduled post until 20 minutes after (times saved while Docker runs), and otherwise leaves it off without hourly warnings. To stop Docker starting with Windows, disable Docker Desktop in Task Manager, Startup apps;
- **dead man's switch (optional):** pings `HEALTHCHECKS_PING_URL` (Healthchecks.io) every run. If the pings stop because the PC is off, asleep or offline, Healthchecks.io alerts you on Telegram from outside.

It has a 15-min cooldown per container and never stops or removes anything. See `infra/ops/windows/README.md` for install, pause and dry-run.

**Postiz startup gate.** `postiz-runtime/docker-compose.yaml` makes the Postiz app wait for Temporal before starting. Docker ignores `depends_on` when Docker Desktop restarts containers itself, which is what silently broke publishing on 16 Nov 2026.

**One limit code can't fix.** The PC must be on and awake at posting times. The Healthchecks.io ping tells you when it isn't. Turn on "Start Docker Desktop when you sign in" and avoid sleep during posting windows.

## 4. Storage Locations & Retention

- **Repository Root:** `C:\Media OS Agent\media-intelligence-os\`
- **External Runtime Secrets (Outside Git):** `C:\Media OS Agent\media-os-runtime\.env` (NTFS ACL locked)
- **External Backup Artifacts (Outside Git):** `C:\Media OS Agent\media-os-backups\` (`.dump` + `.dump.sha256`)
- **Docker Volumes:** `media_os_pgdata` (PostgreSQL 17 cluster) and `media_os_redisdata` (Redis AOF persistence).
- **Retention Governance (`db/retention.py`):** YouTube metadata & metrics are strictly capped at `<= 30` days (`YOUTUBE_POLICY_MAX_DAYS = 30`). Temporary assets follow `MEDIA_TEMP_SUCCESS` (`TEMP_SUCCESS`, `0` days post-QA) and `MEDIA_TEMP_FAILED` (`TEMP_FAILED`, `7` days via `RETENTION_FAILED_TEMP_MEDIA_DAYS=7`), and are protected while referenced as a `BrandProfile` logo or linked to an active/scheduled `ContentPackage`; `MEDIA_FINAL` assets are retained until explicitly deleted. `run_retention_evaluation(..., dry_run=True)` previews cleanup counts without deleting expired records.

---

## 5. Backup & Recovery Strategy

- **On-Demand / Scheduled Backups:** `python scripts/backup_db.py` creates custom-format PostgreSQL dumps (`pg_dump -Fc`) with `.dump.sha256` sidecars and runs `pg_restore --list` validation.
- **Isolated Restore Verification:** `python scripts/restore_verify.py` restores the archive into a disposable temporary database inside `media-os-postgres` (`media_os_restore_verify_<timestamp>_<hex>`), checks Alembic revision `0004_add_brand_topic_assignments`, compares live vs restored SHA-256 content hashes (brands, schedules, jobs, non-secret owner identity) and table counts, and drops the temporary DB afterward.
- **Postiz Independence:** `postiz-runtime` maintains its own `postiz-postgres` backups and `postiz-uploads` / `postiz-config` volumes separately.

---

## 6. Degraded Modes & Failure Scenarios

| Scenario | Verified / Enforced Behavior |
|---|---|
| **Laptop sleeps / hibernates** | Containers pause; `media-os-scheduler` uses `ZoneInfo` + anti-burst advancement (`compute_next_run_at`) on wake so overdue interval/daily slots do **not** burst-fire multiple backlogged jobs. |
| **Worker crashes mid-job** | `JobRun` remains `RUNNING` until `lease_expires_at` passes (`recover_stale_leases()`), transitioning stale runs to `FAILED_RETRYABLE` and re-queueing an `OutboxEvent` without double-running while lease is active. |
| **Redis broker temporarily unavailable** | `JobRun` + `OutboxEvent` commit durably in PostgreSQL first; `dispatch_outbox_batch()` records `FAILED_RETRYABLE` exponential backoff and replays once Redis returns. Login rate limiter follows `RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE` (`false` → bounded in-memory mirrored fallback; `true` → HTTP `503`). |
| **PostgreSQL temporarily unavailable** | FastAPI returns clean `HTTP 503` (`DATABASE_UNAVAILABLE`) on `/health/ready` and authenticated routes without exposing connection strings in HTTP responses or JSON logs. |
| **Postiz unavailable** | Media OS dashboard/API health (`persistent_storage: true`) and local jobs stay operational; Postiz reachability check reports degraded status without crashing API startup. |
