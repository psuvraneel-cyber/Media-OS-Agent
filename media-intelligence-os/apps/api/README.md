# Backend API & Persistence Core (`apps/api`)

**Current Stage (Phase 4):** Python `3.12` FastAPI control plane (`media_os_api.main:create_app`), synchronous SQLAlchemy 2.x + `psycopg` data layer, Alembic migrations (`apps/api/migrations`), Celery JSON worker, transactional outbox dispatcher, and persistent scheduler alongside the preserved Phase 1 standard-library `/health` baseline (`media_os_api.health`).

Implements:
- `GET /health/live` & `GET /health/ready` (verifying real PostgreSQL + Alembic migration state `0003_domain_integrity_hardening` and Redis `PING`)
- Persistent single-owner Argon2id identity (`OwnerUser` + `AuthSession` revocation surviving restarts, CLI `rotate_password`, Redis sorted-set login rate limiting)
- Multi-brand normalized data model (`db/models.py`), service-scoped cross-brand guards (`services/brand_service.py`), PostgreSQL triggers, and retention registry (`db/retention.py`, enforcing `<= 30`-day YouTube compliance)
- Durable job ledger (`JobRun` + `OutboxEvent` with `FOR UPDATE SKIP LOCKED`), JSON-only Celery worker (`concurrency=1`), stale-lease recovery, and DST-aware scheduler (`jobs/scheduler_runner.py`)
- Validated 19-state `ContentPackage` workflow state machine (`workflow/state_machine.py`) with `HOLD` resume lock and idempotent audit trails
- Authenticated brand management & activation APIs (`/api/v1/brands`, `/api/v1/brands/{ref}/activation`, `/api/v1/channels`, `/api/v1/system/audit-events`, `/api/v1/system/status`)

**Deferred (Phase 5+):** Live research source connectors, Gemini AI/TTS adapters, Playwright/FFmpeg creative rendering, OAuth flows, and production Postiz publishing adapter.
