# Operations & Reliability Guide — Media Intelligence OS

**Document Status:** Phase 4 Operations & Reliability Runbook
**Last Updated:** 2026-11-10

---

## 1. Expected Runtime Topology

Media Intelligence OS is designed as two **independent** local Docker Compose deployments running on Docker Desktop (WSL2 Linux engine):

1. **Stack A — `media-os-baseline` (Phase 1) → `media-intelligence-os` (Phase 4 Active Stack):**
   - **Phase 1 Active Baseline:** Single minimal health service (`infra/docker/compose.baseline.yml`) bound to `127.0.0.1:8000` on isolated network `media-os-baseline-net`.
   - **Phase 4 Persistence & Durable Jobs Stack (`infra/docker/compose.app.yml`):** 6 containers on isolated `media-os-net`: `media-os-web` (`127.0.0.1:3000`), `media-os-api` (`127.0.0.1:8000`), `media-os-postgres` (PostgreSQL 17, `5432` internal), `media-os-redis` (Redis 7.2, `6379` internal), `media-os-worker` (Celery `--concurrency=1`), and `media-os-scheduler` (outbox dispatcher + persistent scheduler + lease reaper). *(Future Phase 5/7 `research` & `creative` workers will bind to reserved queues.)*
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
Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/health"

# Safe stop (NEVER `-v`! Stop custom app before running `pytest tests/smoke` because Phase 1 also binds 127.0.0.1:8000)
docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml down
```

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

## 4. Storage Locations & Retention

- **Repository Root:** `C:\Media OS Agent\media-intelligence-os\`
- **External Runtime Secrets (Outside Git):** `C:\Media OS Agent\media-os-runtime\.env` (NTFS ACL locked)
- **External Backup Artifacts (Outside Git):** `C:\Media OS Agent\media-os-backups\` (`.dump` + `.dump.sha256`)
- **Docker Volumes:** `media_os_pgdata` (PostgreSQL 17 cluster) and `media_os_redisdata` (Redis AOF persistence).
- **Retention Governance (`db/retention.py`):** YouTube metadata & metrics are strictly capped at `<= 30` days (`YOUTUBE_POLICY_MAX_DAYS = 30`). Temporary assets follow `MEDIA_TEMP_SUCCESS` (`TEMP_SUCCESS`, `0` days post-QA) and `MEDIA_TEMP_FAILED` (`TEMP_FAILED`, `7` days via `RETENTION_FAILED_TEMP_MEDIA_DAYS=7`), and are protected while referenced as a `BrandProfile` logo or linked to an active/scheduled `ContentPackage`; `MEDIA_FINAL` assets are retained until explicitly deleted. `run_retention_evaluation(..., dry_run=True)` previews cleanup counts without deleting expired records.

---

## 5. Backup & Recovery Strategy

- **On-Demand / Scheduled Backups:** `python scripts/backup_db.py` creates custom-format PostgreSQL dumps (`pg_dump -Fc`) with `.dump.sha256` sidecars and runs `pg_restore --list` validation.
- **Isolated Restore Verification:** `python scripts/restore_verify.py` restores the archive into a disposable temporary database inside `media-os-postgres` (`media_os_restore_verify_<timestamp>_<hex>`), checks Alembic revision `0003_domain_integrity_hardening`, compares live vs restored SHA-256 content hashes (brands, schedules, jobs, non-secret owner identity) and table counts, and drops the temporary DB afterward.
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
