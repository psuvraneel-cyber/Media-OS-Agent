# Phase 5 Completion Plan — Real Research Ingestion

**Status:** Plan accepted for execution milestone-by-milestone. Milestone 1 implemented; Milestone 2
(migration `0005`) awaits owner approval of the schema below before it is written or applied.
**Date:** 2026-11-13
**Baseline:** `269b326` (`feat(research): implement phase 5 research ingestion and connector invariants`)

---

## 0. Starting point (verified, not reported)

| Area | Verified state |
|---|---|
| Ingestion core | `services/research_service.py` enforces SourceItem/TopicCluster dedupe, provenance links and one `PENDING_EVALUATION` assignment per active brand. Works. |
| Connectors | `research/connectors/*.py` are **payload parsers only** — no HTTP, endpoints, rate limiting, retry, conditional requests, checkpoints or health. |
| Jobs | `media_os.research.ingest` accepts raw `items[]` inside `JobRun.payload` (raw untrusted text persisted in the ledger). Worker consumes `control,maintenance,default` only — the `research` queue has **no consumer**. |
| Scheduling | `schedule_definitions.brand_profile_id` is `NOT NULL`, so global (non-brand) research schedules cannot be expressed today. |
| UI / API | No research or topic endpoints; Research/Topics pages are stubs. |
| Runtime DB | Reset by the integration suite at ~14:18Z on 2026-11-13 (see `PHASE_5_STAGE1_DATA_INTEGRITY.md`). |

---

## 1. Milestones

Each milestone is one or more reviewable commits, gated by its own tests. No milestone enables a
source, a schedule, or a live external fetch.

### M1 — Environment & data integrity (no schema change) — *implemented*

Files:
- `tests/db_safety.py` (new) — URL guards for disposable test DB/Redis.
- `tests/integration/conftest.py` (new) — session-wide autouse guard; aborts the run on unsafe targets.
- `tests/unit/test_db_safety.py` (new).
- `infra/docker/compose.test.yml` (new) — ephemeral `media-os-test` project (tmpfs Postgres 17 + Redis 7.2, loopback ports 55432/56379, no named volumes).
- `scripts/run_integration_tests.py` (new) — up → migrate → pytest → down for the disposable stack.
- `apps/api/src/media_os_api/research/tasks.py` — manual-ingest payload hygiene (item cap, field allowlist, 500-char sanitisation, scrub raw items on success, content hash for idempotency).
- `apps/api/src/media_os_api/jobs/ledger.py` — idempotent re-enqueue recognises scrubbed payloads.
- `tests/unit/test_phase5_payload_hygiene.py` (new).
- `docs/PHASE_5_STAGE1_DATA_INTEGRITY.md` (new) — environment state, incident analysis, cleanup proposal, backup/restore evidence.

Acceptance: unit suite green; guard refuses `media_os`, `media-os-postgres`, Redis db 0, and any URL equal to `DATABASE_URL`; integration suite passes against the disposable stack; zero rows changed in runtime DB.

### M2 — Migration `0005_research_source_registry` — *needs owner approval*

**Is a migration necessary? Yes.** Checkpoints, conditional-request validators and source health must
survive worker restarts and laptop sleep (Redis is not the system of record — ADR-004/020), and global
research schedules need `schedule_definitions.brand_profile_id` to be nullable.

Proposed schema (additive; no existing column or row is altered except the nullability relaxation):

```text
research_sources                      -- one row per source family; registry + health
  source_key          varchar(32) PK  -- HACKERNEWS | GITHUB | HUGGINGFACE | ARXIV | OPENAI_NEWS | REDDIT
  display_name        varchar(128) not null
  policy_mode         varchar(16)  not null default 'OFF'
                      CHECK policy_mode IN ('OFF','EXPERIMENTAL','ENABLED','BLOCKED')
                      CHECK (source_key <> 'REDDIT' OR policy_mode = 'BLOCKED')   -- Reddit lock in DB
  live_fetch_approved boolean      not null default false
                      CHECK (NOT live_fetch_approved OR policy_mode IN ('EXPERIMENTAL','ENABLED'))
  approved_by         varchar(128) null
  approved_at         timestamptz  null
  config              jsonb        not null default '{}'   -- non-secret params (categories, queries, limits)
  health_status       varchar(16)  not null default 'UNKNOWN'
                      CHECK health_status IN ('UNKNOWN','HEALTHY','DEGRADED','FAILING','DISABLED','BLOCKED')
  consecutive_failures integer     not null default 0
  last_attempt_at / last_success_at / last_failure_at   timestamptz null
  last_error_code     varchar(64)  null
  last_error_message  varchar(500) null                  -- redacted, truncated
  last_run_stats      jsonb        not null default '{}' -- fetched/new/duplicate/malformed counts
  backoff_until       timestamptz  null                  -- circuit breaker / Retry-After
  created_at / updated_at

research_source_checkpoints           -- one row per stream within a source
  id                  uuid PK
  source_key          varchar(32) FK -> research_sources ON DELETE RESTRICT
  stream_key          varchar(128) not null              -- e.g. 'topstories', 'cat:cs.AI', 'models'
  cursor              jsonb not null default '{}'        -- high-water marks, seen-id window
  etag                varchar(512) null
  last_modified       varchar(128) null
  last_fetched_at / last_success_at  timestamptz null
  UNIQUE (source_key, stream_key)

schedule_definitions
  brand_profile_id    -> NULLABLE (global research schedules)
  + partial unique index uq_schedule_definitions_global_name ON (name) WHERE brand_profile_id IS NULL
```

Data step: insert the six registry rows — five first-cohort sources `OFF`, `live_fetch_approved=false`;
`REDDIT` `BLOCKED`. Five `schedule_definitions` (`research.fetch.<source>`) with `is_enabled=false`.

Downgrade: drops the two new tables and the partial index; restores `NOT NULL` only if no global
schedule rows exist (otherwise refuses). No editorial data (`brand_topic_assignments`) is touched.
Applying to the **runtime** DB requires: owner approval, fresh `backup_db.py`, `restore_verify.py`.

Files: `migrations/versions/0005_research_source_registry.py`, `db/models.py` (`ResearchSource`,
`ResearchSourceCheckpoint`), `research/registry.py` (read/update helpers, health transitions),
`tests/integration/test_phase5_migration_0005.py`, extension of the migration inventory test.

### M3 — Shared connector infrastructure

`research/http/`:
- `policy.py` — `HostPolicy(allowed_hosts, max_bytes, timeouts)`; HTTPS only; exact-host allowlist.
- `ssrf.py` — resolve every host with `getaddrinfo`; reject unless every address is
  `ipaddress.is_global` (blocks loopback, RFC1918, link-local/metadata `169.254.169.254`, ULA, CGNAT,
  multicast, IPv4-mapped IPv6). Residual DNS-rebinding TOCTOU documented; mitigated by allowlist.
- `client.py` — `SafeHttpClient` over `httpx` (already pinned): `follow_redirects=False`, manual
  redirect handling (≤3 hops, each hop re-validated against scheme/allowlist/IP), connect 5 s /
  read 15 s / pool 5 s, streamed body with hard byte cap (default 2 MiB), User-Agent
  `MediaIntelligenceOS/0.5 (+local research ingestion)` with no personal data, conditional
  `If-None-Match` / `If-Modified-Since`, optional bearer token injected from env only for GitHub and
  never logged or stored in payloads.
- `ratelimit.py` — Redis minimum-interval limiter (`SET key NX PX interval`, bounded wait); fails
  **closed** on Redis outage (raises retryable), consistent with ADR-021.
- `retry.py` — classification: timeouts / connect errors / 5xx / 408 / 429 → retryable (honour
  `Retry-After`, capped 120 s); other 4xx, oversize, policy/SSRF violations, parse errors → permanent.
  In-run retries ≤2 with exponential backoff + jitter; beyond that the JobRun ledger retries.

`research/fetchers/base.py` — the common contract:

```python
class SourceFetcher(Protocol):
    source_key: str
    host_policy: HostPolicy
    min_interval_ms: int

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]: ...
    def fetch_page(
        self, http: SafeHttpClient, stream: StreamSpec, checkpoint: CheckpointState
    ) -> FetchPage: ...  # fetch + checkpoint
    def to_candidates(
        self, record: Mapping[str, Any]
    ) -> list[RawDiscoveryCandidate]: ...  # validate + normalise
```

`research/runner.py` — `run_source_fetch(session_factory, source_key, *, http, limiter, live_gate)`:
1. Gate: registry `policy_mode in (EXPERIMENTAL, ENABLED)` **and** `live_fetch_approved` **and** env
   `RESEARCH_LIVE_FETCH_ENABLED=true`; else `PolicyRefusalError` (permanent, no HTTP issued).
2. Per stream: fetch page → per record, in a SAVEPOINT: validate → normalise → `ingest_discovery_candidate`.
   Malformed records are counted and skipped; they never abort the page.
3. Checkpoint written in the same transaction as the records it covers (crash ⇒ re-fetch, dedupe absorbs it).
4. Health recorded in a separate transaction even on failure. One stream failing does not stop other streams.

`services/research_service.py` hardening: `INSERT … ON CONFLICT DO NOTHING` for SourceItem,
TopicCluster, TopicClusterSource and BrandTopicAssignment so concurrent workers cannot raise
`IntegrityError`; invariants (NULL score, PENDING_EVALUATION, global-field protection) unchanged.

### M4a–M4e — Five connectors, one commit each

| Source | Endpoint (official) | Streams / bounds | Checkpoint | Notes |
|---|---|---|---|---|
| Hacker News | `https://hacker-news.firebaseio.com/v0/{top,new,best}stories.json`, `/v0/item/{id}.json` | ≤30 ids per list; skip ids already ingested | seen-id window + max id | `type=story`, not `dead`/`deleted`; `text` sanitised (≤500) |
| arXiv | `https://export.arxiv.org/api/query?search_query=cat:<c>&sortBy=submittedDate&sortOrder=descending&max_results=50` | `cs.AI`, `cs.CL`, `cs.LG`, `cs.SE` | newest `published` per category | ≥3.1 s spacing via limiter; Atom parsed with `defusedxml`; no PDF links followed |
| OpenAI News | `https://openai.com/news/rss.xml` | single feed | ETag / Last-Modified + newest `pubDate` | 304 ⇒ no work; bot-challenge/403 recorded as FAILING, never circumvented |
| GitHub | `https://api.github.com/search/repositories?q=<topic query> pushed:>D&sort=updated&per_page=30`; `GET /repos/{o}/{r}/releases?per_page=5` for a configured watch-list | ≤30 repos, ≤20 watched repos | ETag per request + `pushed` high-water | Honour `X-RateLimit-Remaining/Reset`; token optional, read-only, env only |
| Hugging Face | `https://huggingface.co/api/{models,datasets,spaces}?sort=lastModified&direction=-1&limit=30` | three streams | newest `lastModified` per stream | Metadata only; no `huggingface_hub`, no file downloads |

New dependency: **`defusedxml`** (pure Python, ~25 kB) — rejects DTD/entity expansion in untrusted
Atom/RSS. No other new dependency.

### M5 — Source-specific jobs & disabled schedules

- Task names `media_os.research.fetch.<source>`; payload `{"source_key", "trigger"}` only — **no content**.
- Enqueued via `enqueue_durable_job` (JobRun + OutboxEvent, one transaction); idempotency key
  `research:<source>:<schedule slot>`.
- Schedules seeded by `0005` with `is_enabled=false`.
- Compose: add `research` to the worker's `-Q` list (still `--concurrency=1`). Deploying this to the
  running stack is an owner-approved rebuild/restart.
- Owner CLI `python -m media_os_api.research.admin` — `status`, `approve-live --source X`,
  `set-mode --source X --mode EXPERIMENTAL|OFF`; every change writes an `AuditEvent`. Refuses Reddit.

### M6 — Read-only API + dashboard

- `GET /api/v1/research/sources` — registry, policy mode, approval flag, health, checkpoints.
- `GET /api/v1/research/items?provider=&cursor=` — SourceItems with cluster id, dedupe key, discovery time.
- `GET /api/v1/topics?brand=<ref>&status=` — clusters with **the selected brand's** assignment only,
  provenance count, first/last seen.
- `GET /api/v1/topics/{id}?brand=<ref>` — provenance list + that brand's assignment.
All authenticated, read-only, brand-scoped where brand data is involved; no other brand's editorial
state is returned. Replace `ResearchView`/`TopicsView` stubs; Vitest coverage.

### M7 — Documentation reconciliation

Authoritative `docs/PHASE_5_IMPLEMENTATION.md`; correct both READMEs (Phase 5 status, Postiz
`v2.25.3` not `v1.47.0`), `ARCHITECTURE.md`, `PRD.md` source table, `OPERATIONS.md`,
`REQUIREMENTS_TRACEABILITY.md`. Live verification reported separately from fixture tests.

---

## 2. Test strategy

| Layer | What | Where it runs |
|---|---|---|
| Unit | URL guards, SSRF (private IP, redirect-to-private, `http://`, non-allowlisted host, oversize body), retry classification, rate limiter (in-memory fake), each fetcher's parse/validate against hand-written fixtures in `tests/fixtures/research/` via `httpx.MockTransport`, pagination, 304 handling, malformed records | offline, no Docker |
| Integration | migration `0005` up/down, runner end-to-end with mock transport + real Postgres/Redis: duplicate ingestion, checkpoint recovery after mid-stream crash, concurrent ingestion (two threads, same items), source isolation (one fails, others succeed), Redis-outage fail-closed, gate refusal issues zero HTTP | **disposable `media-os-test` stack only** (guard enforced) |
| Live smoke | one bounded run per source (≤5 records) against the disposable test DB, timestamped log | **only after owner approval per source** |

---

## 3. Security considerations

Host allowlist + HTTPS + DNS/IP validation per hop; byte caps; no credentials in payloads, logs, DB or
frontend; GitHub token optional and read-only; source text never interpreted as instructions and capped
at 500 chars after HTML stripping; Reddit blocked in code, in the registry `CHECK`, and in the admin CLI;
live fetch requires three independent switches (registry mode, approval flag, env kill-switch).

## 4. Resource impact (16 GB laptop)

No new runtime containers. Worker stays `--concurrency=1`; fetches are sequential with ≤2 MiB bodies
(peak worker RSS increase estimated <30 MiB). The disposable test stack (~150–200 MiB) exists only while
`run_integration_tests.py` runs. One small new dependency.

## 5. Operations requiring owner approval

1. Approve migration `0005` schema (this document) — then applying it to the runtime DB.
2. Cleanup of fixture rows in the runtime DB (proposal in `PHASE_5_STAGE1_DATA_INTEGRITY.md`).
3. Rebuilding/restarting runtime containers to deploy new code (worker queue change).
4. Each live smoke test (per source).
5. Setting any source to `EXPERIMENTAL` with `live_fetch_approved`, and enabling any schedule.
6. Supplying an optional read-only GitHub token.
