# Phase 5 Completion Plan — Real Research Ingestion

**Status:** Plan accepted for execution milestone-by-milestone. M1 implemented (`849cf94`). M2 schema
approved by the owner 2026-11-13, implemented (`2ae27bd`) and **applied to the runtime DB on
2026-11-13 16:10Z with owner approval** (evidence in §M2 "Runtime application record"). M3 shared
connector infrastructure implemented (`4fde588`). M4a Hacker News fetcher implemented and
fixture/DB-tested; live verification pending owner approval. Nothing deployed to the running stack.
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

**Implementation notes (M2, as built):**
- Column names are `source_code` / `stream_name` instead of `source_key` / `stream_key`: the existing
  job-payload secret guard (`validate_safe_task_payload`) and log redaction treat every `*_key` field
  as a credential, so `*_key` names would be rejected in payloads and masked in logs. Schedule
  payloads use `{"source": ..., "trigger": "schedule"}` for the same reason.
- Added beyond the approved sketch (stricter, no behavioural change): `source_code` format check,
  `approved_by`/`approved_at` required when `live_fetch_approved`, non-negative failure counter, and
  `CHECK (brand_profile_id IS NOT NULL OR channel_account_id IS NULL)` on `schedule_definitions`.
- Seed: health `DISABLED` for the five `OFF` sources, `BLOCKED` for Reddit. Schedule IDs are
  deterministic (uuid5) so downgrade/upgrade is repeatable. Cadences (all disabled): HN 1 h,
  GitHub 6 h, Hugging Face 6 h, arXiv daily 07:00 Asia/Kolkata, OpenAI News 3 h.
- Live gate = `policy_mode ∈ {EXPERIMENTAL, ENABLED}` ∧ `live_fetch_approved` ∧
  `RESEARCH_LIVE_FETCH_ENABLED=true`; refusals are permanent `PolicyRefusalError`s with specific
  codes; an active per-source backoff is a retryable `SOURCE_BACKOFF_ACTIVE`.
- Health: success → `HEALTHY`; failures → `DEGRADED`, then `FAILING` at 3 consecutive; backoff
  60 s · 2ⁿ⁻¹ capped at 6 h, or the server's `Retry-After` (capped 6 h). Error text is redacted and
  truncated to 500 chars.
- Known follow-up for M5: `run_scheduler_tick` hard-codes `queue_name="control"`; research jobs must
  be routed to the `research` queue and the worker must consume it.

**Runtime apply procedure (when approved):**
1. `python scripts/backup_db.py --env-file "C:\Media OS Agent\media-os-runtime\.env"`
2. `python scripts/restore_verify.py --env-file "C:\Media OS Agent\media-os-runtime\.env"`
3. Deploy the new image (rebuild `media-os-api`, restart api/worker/scheduler) or run
   `docker exec media-os-api alembic upgrade head` from an image that contains 0005.
4. Verify: `alembic_version = 0005_research_source_registry`, 6 registry rows, 5 disabled schedules.

**Runtime application record (2026-11-13, owner-approved):**

| Step | Evidence |
|---|---|
| Pre-state 16:09:35Z | `0004`; brands 2, source_items 7, topic_clusters 8, links 6, assignments 14, research_packets 4, content_packages 4, channels 2, schedules 0, job_runs 1, outbox 1, audit 3, in-flight jobs 0 |
| Pre-backup | `media_os_20261113T160936Z.dump` — TOC-verified; restore-verified in `media_os_restore_verify_20261113160936_fbe398` (schema `0004`, brands_sha_match=True) |
| Image check | Running `media-intelligence-os-api:phase4` (built 2026-11-10) has `env.py` and 0001–0003 byte-identical (CRLF-normalised) to this branch, **no 0004/0005 files and no `research` package** — the deployed app is Phase 4 code. |
| Apply | `docker stop media-os-scheduler` (16:09:59Z) → `docker compose --env-file …\media-os-runtime\.env -f …\media-intelligence-os\infra\docker\compose.app.yml run --rm --no-deps -T -v <branch>\apps\api\migrations:/app/apps/api/migrations:ro media-os-api alembic upgrade head` → `Running upgrade 0004 -> 0005`, exit 0 → `docker start media-os-scheduler` (16:10:01Z). No app image rebuilt; no other container touched. |
| Post-state 16:10:11Z | `0005`; every pre-existing count identical; schedules 5 (all `is_enabled=false`, global); 6 registry rows (5 `OFF`/`DISABLED`, Reddit `BLOCKED`); 0 checkpoints; `brand_profile_id` nullable; live `UPDATE … REDDIT → ENABLED` rejected by `ck_…_reddit_blocked` (rolled back) |
| Health | scheduler `healthy`, 0 error lines since restart; `/health/ready` database `ready (schema=0005_research_source_registry)`; web `/api/health` 200 |
| Post-backup | `media_os_20261113T161037Z.dump` — restore-verified in `media_os_restore_verify_20261113161038_1f8bf0` (schema `0005`, schedule_definitions 5, brands_sha_match=True) |
| Rollback | `alembic downgrade 0004_add_brand_topic_assignments` (refuses if foreign global schedules exist) or restore `media_os_20261113T160936Z.dump` |

Pre-existing quirk observed (not introduced by 0005): `env.py` passes the naming-convention metadata
to Alembic, so explicitly named CHECK constraints are double-prefixed in every migration
(e.g. `ck_source_items_ck_source_items_source_policy_mode_valid`, `ck_research_sources_ck_research_
sources_reddit_blocked`), with long names truncated + hashed. Functionally correct and consistent
between upgrade and downgrade; tracked as tech debt (fixing it requires a rename migration).

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

**Implementation notes (M3, as built):**
- Package `research/http/` (`policy.py`, `ssrf.py`, `retry.py`, `ratelimit.py`, `client.py`,
  `errors.py`); `research/fetchers/base.py`; `research/runner.py`. No new dependency (`httpx` and
  `redis` were already pinned).
- Fetch errors subclass the ledger's `RetryableTaskError` / `PermanentTaskError`, so the existing
  job classification applies unchanged. Codes: `HTTP_<status>`, `HTTP_403_RATE_LIMITED`,
  `HTTP_TIMEOUT`, `HTTP_NETWORK`, `UNSAFE_URL`, `RESPONSE_TOO_LARGE`, `TOO_MANY_REDIRECTS`,
  `DNS_RESOLUTION_FAILED`, `RATE_LIMITER_UNAVAILABLE`, `RATE_LIMIT_WAIT_EXCEEDED`,
  `MALFORMED_DOCUMENT`, `SOURCE_FETCH_FAILED`.
- Client extras beyond the plan: `trust_env=False` (ambient proxy variables ignored); connected-peer
  address re-checked when the transport exposes it (DNS-rebinding defence in depth); Authorization
  only sent to `auth_hosts` and therefore dropped on cross-host redirects.
- In-run retries: 3 attempts, full-jitter exponential backoff (cap 30 s); `Retry-After` ≤ 60 s is
  slept exactly, longer waits are raised so the registry records a per-source backoff instead of
  holding the single worker slot.
- Fetchers declare `allowed_namespaces`; any other emitted namespace (e.g. `REDDIT_POST`) is
  rejected as malformed before reaching ingestion. Registry `min_interval_ms` can only make spacing
  stricter than the fetcher's floor.
- Partial runs (some streams failed) record `DEGRADED` without backoff; all-streams-failed records
  backoff and raises permanent `SOURCE_FETCH_FAILED` — the next scheduled slot is the retry.
- Known limitation: two concurrent runs of the same source are *safe* (ON CONFLICT, verified) but
  not serialised; scheduled runs are already de-duplicated per slot by the JobRun idempotency key.

**M3 verification:** unit 127 passed (55 new HTTP-layer tests); integration (disposable stack)
45 passed / 2 skipped (8 new runner tests). Mutation check: the two concurrency tests fail against
the pre-M3 ingestion code with `UniqueViolation` on `uq_source_items_provider_external_id`, and pass
with the ON CONFLICT version. No live external request was made.

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

**Connector status**

| Source | Fetcher | Fixture tests | DB integration | Live verification |
|---|---|---|---|---|
| Hacker News | `research/fetchers/hackernews.py` (M4a) | 24 unit | 3 end-to-end | **Not run** — awaiting owner approval (`scripts/research_live_smoke.py --source HACKERNEWS --owner-approved`) |
| arXiv | — (M4b) | — | — | — |
| OpenAI News | — (M4c) | — | — | — |
| GitHub | — (M4d) | — | — | — |
| Hugging Face | — (M4e) | — | — | — |

**M4a Hacker News (as built):**
- Official endpoints only: `https://hacker-news.firebaseio.com/v0/{topstories,newstories,beststories}.json`
  and `/v0/item/{id}.json`; allowlist = that single host; no credentials; 1 MiB body cap; ≥250 ms
  spacing (registry may only tighten).
- First `max_items_per_stream` ids per list (default 30, hard cap 100). Checkpoint keeps a bounded
  `seen_ids` window (1000) per stream, so steady state is one list request per stream plus new items.
  Item documents are cached per run across streams.
- Per-item isolation: transient item failures are skipped and retried next run; permanent ones
  (404, oversize, malformed JSON) are marked seen; if every item in a stream fails, the stream fails.
  Isolated sub-request failures are reported per stream as `fetch_errors` (new `FetchPage` field)
  and stored in `last_run_stats`.
- Validation: positive integer id, `type == "story"`, not dead/deleted (others filtered, not
  malformed); non-empty title; integer timestamp; only `http(s)` URLs kept (others fall back to the
  HN discussion URL). Normalisation reuses the existing `HackerNewsConnector` parser.
- Firebase exposes no standard ETag/Last-Modified, so HN uses the seen-window instead of conditional
  requests.
- `sanitize_minimal_summary` now decodes HTML entities and re-strips tags (so `&lt;script&gt;` cannot
  survive decoding) and drops control characters — applies to all sources.
- `scripts/research_live_smoke.py`: owner-gated (`--owner-approved` required), ≤10 items, one stream,
  disposable test DB only, prints a timestamped JSON evidence record. Plumbing verified offline with
  the HN fixtures; **no live request has been made**.
- Gap carried forward: HN `score` / `descendants` are parsed into candidate metadata but
  `SourceItem` has no column for source metrics, so they are not persisted. Needed by Phase 6
  ranking (FR-03); decide on storage before M6/Phase 6.

**M4a verification:** unit 151 passed (24 new HN tests incl. sanitizer); integration 48 passed /
2 skipped (3 new HN end-to-end: full ingestion with dedupe/provenance/assignments/checkpoint and a
3-request steady-state second run; partial outage isolation; gate blocks all traffic).

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
