# Phase 5 Stage 1 — Environment & Data Integrity Report

**Date:** 2026-11-13 · **Scope:** read-only investigation + test-safety changes. **No runtime row was
modified or deleted.**

---

## 1. Environment state (observed 2026-11-13 ~15:40Z)

| Item | State |
|---|---|
| Media OS containers | `media-os-{web,api,worker,scheduler,postgres,redis}` — all healthy; images `media-intelligence-os-{api,web}:phase4` |
| Postiz | 8 containers healthy; `ghcr.io/gitroomhq/postiz-app:v2.25.3` (verified from the running image tag) |
| Runtime DB | `media_os` on `media-os-postgres`, Alembic head `0004_add_brand_topic_assignments` |
| Other DBs on the runtime cluster | `media_os_test` — **exists but is empty (no schema)**; `postgres`, `template0/1` |
| Worker queues | `control,maintenance,default` (no `research` consumer) |
| Schedules | 0 `schedule_definitions` rows |
| Unrelated container | `media-os-test-proxy` (exited 8 h before this session; not created by this work; left untouched) |

## 2. Incident: the integration suite ran against the runtime database

Evidence:

1. `brand_profiles.created_at` = `2026-11-13 14:18:07Z` for both seeded brands — the brands were
   re-inserted, not carried over.
2. `tests/integration/test_phase4_postgres_and_migrations.py` executes
   `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`, downgrades to base and upgrades to head.
3. Every non-brand row in `media_os` is a known test fixture, created in two bursts
   (`14:18:07–14:18:10Z` and `14:39:17Z`).
4. Restore-verification of the pre-incident backup `media_os_20261113T081539Z.dump` shows the DB held
   `owner_users=1, audit_events=2` and **zero** channels/jobs/content/assignments at 08:15Z.
5. `media_os_test` exists but has no schema, i.e. the intended test DB was never used.

Impact: the runtime DB was reset. Lost relative to 08:15Z: the `owner_users` row (it is re-created
from `OWNER_PASSWORD_HASH` by `ensure_owner_bootstrapped` on the next login — if the password was
ever rotated with `rotate_password`, the env hash is now authoritative again) and 2 audit events.
No editorial data existed to lose. A destructive retention run (`RETENTION_CLEANUP_EXECUTED`,
`deleted_source_items=1, deleted_temp_assets=1`) also executed against the runtime DB as part of the
same test run; it only deleted fixture rows.

## 3. Safeguards added (Milestone 1)

| Safeguard | File |
|---|---|
| Guard refuses: runtime hosts `media-os-postgres`/`media-os-redis`; DB names `media_os`/`postgres`/templates; any DB name not ending `_test`; Redis logical DB 0; a non-disposable `DATABASE_URL` present during the run. Errors never echo credentials. | `tests/db_safety.py` |
| Enforced for every integration run at `pytest_configure` → `pytest.exit(returncode=4)` before any connection. | `tests/integration/conftest.py` |
| Disposable stack: project `media-os-test`, tmpfs Postgres 17 (`127.0.0.1:55432`, DB `media_os_it_test`), Redis 7.2 (`127.0.0.1:56379`, db 15), no named volumes, 256 MiB + 64 MiB limits. | `infra/docker/compose.test.yml` |
| One command: up → `alembic upgrade head` → pytest → `down` (Postiz probes excluded by default). | `scripts/run_integration_tests.py` |
| Runtime-touching backup test now opt-in (`RUN_LIVE_BACKUP_TESTS=1`); asserts current Alembic head instead of stale `0003`. | `tests/integration/test_phase4_backup_and_restarts.py` |

Proof the guard works:
`MEDIA_OS_TEST_DATABASE_URL=postgresql+psycopg://u:p@media-os-postgres:5432/media_os pytest tests/integration/...`
→ `Exit: [TEST-TARGET-GUARD] Refusing test database on runtime host 'media-os-postgres'.`

## 4. Raw source content in `JobRun.payload`

Finding: `media_os.research.ingest` accepted unbounded raw records in `items[]`, persisted verbatim in
`job_runs.payload` (the 500-char policy applied only to `SourceItem`). Runtime check: **0**
`media_os.research.ingest` JobRuns exist, so no raw content is currently retained.

Remediation (Milestone 1):
- `build_manual_ingestion_payload()` sanitises before enqueue: ≤50 items, flat scalar fields only,
  HTML stripped and free text capped at 500 chars, other strings ≤2048, lists ≤50 scalars, item digest.
- Handler re-sanitises defensively, isolates malformed records per SAVEPOINT, and on success replaces
  `items` with `{item_count, items_sha256, items_redacted: true}`.
- `enqueue_durable_job` treats a redacted payload as equivalent to the original (same digest) so
  idempotent re-enqueue still works after scrubbing.
- Residual: a job that ends `FAILED_FINAL` keeps its (already sanitised) items. Scheduled fetch jobs
  (Milestone 5) carry no content at all.

## 5. Fixture records and obsolete namespace in the runtime DB

| Table | Rows | Origin |
|---|---|---|
| `source_items` | 7 | 1× obsolete namespace `GITHUB` (`repo-123`, `ENABLED`), 2× `ARXIV_PAPER` with invalid ids `2403.434bdv1`/`2403.eaf57v1`, 4× `GITHUB_REPOSITORY` `test-org/gating-repo-*`, `test-worker-repo-*` |
| `topic_clusters` | 8 | 7 above + `trigger-…`, `iso-topic-…` (Phase 4 tests) |
| `topic_cluster_sources` | 6 | links for the above |
| `brand_topic_assignments` | 14 | fixtures (incl. `APPROVED_FOR_RESEARCH`, `REJECTED`, `SHORTLISTED`) |
| `research_packets` | 4 | brand-tech-ai `DRAFT` fixtures |
| `claims` | 1 · `claim_sources` 0 | fixture |
| `content_packages` | 4 · `content_state_transitions` 3 · `assets` 3 | Phase 4 isolation/retention fixtures |
| `channel_accounts` | 2 | fixture Instagram bindings (no Postiz integration behind them) |
| `metrics_snapshots` | 1 | fixture |
| `job_runs` / `outbox_events` | 1 / 1 | `media_os.internal.ping` from the test run |
| `audit_events` | 3 | test actions (`owner:test` reassignment, retention dry-run, retention cleanup) |
| `brand_profiles` | 2 | the two seeded brands (keep) |

## 6. Cleanup — ✅ executed 2026-11-14 (owner-approved Option A)

**Outcome.** The owner approved Option A in chat ("Yes to both").
- **Backup:** `media_os_20261114T100208Z.dump` was taken first and restore-verified
  (schema `0005`, `brands_sha_match=True`).
- **Rehearsal:** the script ran against a restored copy, which was then dropped.
- **Real run:** one transaction, with the scheduler and worker stopped (not `down`).
- **Guards:** checks before and after the deletes would have rolled back if the data had differed
  from the documented fixtures.
- **After:** `source_items`, `topic_clusters`, `brand_topic_assignments`, `content_packages`,
  `channel_accounts` and `job_runs` hold 0 rows.
- **Kept:** 2 brands, 1 owner user, 5 audit events, 5 schedule definitions, 6 research sources.
- **Health:** API `/health/ready` is ready; all six containers are healthy.
- **Rollback:** restore `media_os_20261114T100208Z.dump`.

The original proposal is kept below for the record.


Preconditions (both satisfied today, repeat immediately before execution):
1. Fresh backup: `media_os_20261113T154338Z.dump` — `[OK] PostgreSQL backup created and TOC-verified`.
2. Restore verification: `[OK] Verified restore in isolated temp DB
   'media_os_restore_verify_20261113154339_d1c57a' (schema=0004_add_brand_topic_assignments,
   brands_sha_match=True, rows={… brand_profiles: 2, channel_accounts: 2, job_runs: 1,
   outbox_events: 1, audit_events: 3, content_packages: 4, brand_topic_assignments: 14})`.
3. Stop writers first: `docker stop media-os-scheduler media-os-worker` (not `down`).

**Option A (recommended) — targeted fixture deletion, one transaction, brands untouched:**

```sql
BEGIN;
-- content side (Phase 4 fixtures)
DELETE FROM content_state_transitions WHERE content_package_id IN (
  '9496200a-a256-4a9a-9622-4641ee692130','8636bf00-6d04-4466-a6d1-4bc006181675',
  'cfb64e09-8d5a-4360-83a3-e463e19f3a63','312a6462-e137-4836-8769-52c9b2838576');
DELETE FROM assets WHERE id IN ('661a36a1-529f-4061-b97b-1ba6079ec2c2',
  'a079320e-d6f1-4f35-92ee-6e64878882b3','88b3ae50-6097-4af3-93c6-b067950a2137');
DELETE FROM metrics_snapshots WHERE id = '2ff0e2b3-b349-4cf8-bb87-19715f85edea';
DELETE FROM content_packages WHERE id IN (
  '9496200a-a256-4a9a-9622-4641ee692130','8636bf00-6d04-4466-a6d1-4bc006181675',
  'cfb64e09-8d5a-4360-83a3-e463e19f3a63','312a6462-e137-4836-8769-52c9b2838576');
DELETE FROM claims WHERE id = '20068eea-755c-4cb6-bb82-84384832da04';
DELETE FROM research_packets WHERE id IN (
  '27330dd6-f2bc-46eb-9106-c8e2ec314f32','69b9fe71-9a81-4522-9995-9cf6ec73916a',
  '7f59a6e6-2292-4b29-ae12-db7557f759b2','bc362670-ea85-4381-b7ac-26b81b0cb560');
DELETE FROM channel_accounts WHERE id IN (
  'ed6177c5-1b3c-4267-b763-7ae4901ca02a','9408b717-1e1b-41f7-a3ae-ddcb97475444');
-- research side (all 8 clusters / 7 sources are fixtures, incl. obsolete 'GITHUB' namespace)
DELETE FROM brand_topic_assignments;          -- 14 rows, all reference the 8 fixture clusters
DELETE FROM topic_cluster_sources;            -- 6 rows
DELETE FROM topic_clusters;                   -- 8 rows
DELETE FROM source_items;                     -- 7 rows
-- test ping job
DELETE FROM outbox_events WHERE job_run_id IN
  (SELECT id FROM job_runs WHERE task_name = 'media_os.internal.ping');
DELETE FROM job_runs WHERE task_name = 'media_os.internal.ping';
-- audit_events: KEEP (append-only trail; they document the incident). Optionally annotate.
-- Verify expected counts before committing:
SELECT (SELECT count(*) FROM source_items), (SELECT count(*) FROM topic_clusters),
       (SELECT count(*) FROM brand_topic_assignments), (SELECT count(*) FROM content_packages),
       (SELECT count(*) FROM brand_profiles);   -- expect 0,0,0,0,2
COMMIT;   -- or ROLLBACK if any count differs
```

The unqualified research-table deletes are only correct **today** (every row is a fixture). If any
real ingestion happens first, this script must be regenerated with explicit IDs. Exact FK order will
be validated by running the script against a restored copy (`restore_verify`-style temp DB) before
touching `media_os`.

**Option B — restore the pre-incident backup** `media_os_20261113T081539Z.dump` into `media_os`
(`pg_restore --clean`). Recovers the original `owner_users` row and 2 audit events exactly, but is a
full-database overwrite; requires stopping api/worker/scheduler and first confirming that dump's
Alembic version equals `0004`.

Rollback for either option: restore `media_os_20261113T154338Z.dump`.

## 7. Verification evidence for Milestone 1

- Unit: `57 passed` with `MEDIA_OS_WORKSPACE_ROOT` pointing at the workspace (without it, 2
  `test_precommit_dispatcher` tests fail when run from a git worktree because the reference archives
  are not beside the checkout — location-specific, not a code defect).
- Integration (disposable stack via `scripts/run_integration_tests.py`): `22 passed, 2 skipped`
  (skips: opt-in live backup test, opt-in live restart test).
- Runtime DB row counts before/after the integration run: identical
  (`source_items=7, topic_clusters=8, brand_topic_assignments=14, job_runs=1, audit_events=3`).
- `ruff check`, `ruff format --check`, `mypy` (strict, 93 files): clean.
