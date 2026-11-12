# Docker Deployment (`infra/docker`)

Contains isolated Docker Compose configurations for Media Intelligence OS:

1. **Phase 1 Minimal Baseline (`compose.baseline.yml` + `Dockerfile.baseline`):**
   - Project `media-os-baseline` / network `media-os-baseline-net` / service `baseline-api` on `127.0.0.1:8000`.
   - Retained permanently for `pytest tests/smoke` baseline verification (stop `compose.app.yml` without `-v` before running `tests/smoke`).
2. **Phase 4 Persistent Application Stack (`compose.app.yml` + `Dockerfile.api` + `Dockerfile.web`):**
   - Project `media-intelligence-os` / network `media-os-net` (isolated from `postiz-runtime` networks and volumes).
   - 6 containers: `media-os-postgres` (PostgreSQL 17, internal `5432`, `mem_limit: 512m`), `media-os-redis` (Redis 7.2, internal `6379`, `requirepass` + AOF, `mem_limit: 128m`), `media-os-api` (`127.0.0.1:8000`, `mem_limit: 256m`), `media-os-worker` (Celery `--concurrency=1`, `mem_limit: 256m`), `media-os-scheduler` (outbox/scheduler daemon, `mem_limit: 128m`), and `media-os-web` (`127.0.0.1:3000`, `mem_limit: 256m`).

See [`../../docs/OPERATIONS.md`](../../docs/OPERATIONS.md) and [`../../docs/PHASE_4_IMPLEMENTATION.md`](../../docs/PHASE_4_IMPLEMENTATION.md) for verified commands, backup/restore procedures, and host resource telemetry.
