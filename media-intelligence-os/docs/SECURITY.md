# Security Architecture & Operating Policy — Media Intelligence OS

**Document Status:** Phase 0 Security Contract
**Last Updated:** 2026-11-10

---

## 1. Secret Management

- **No Secrets in Source Control:** API keys, OAuth client secrets, JWT secrets, database passwords, and session tokens must **never** be committed to Git, embedded in source code, included in prompts, or printed in terminal logs.
- **Placeholder Templates Only:** Only `.env.example` files with obvious placeholder values (`REPLACE_WITH_*`) may be committed. Real `.env` files are excluded via `.gitignore`.
- **Automated Secret Scanning:** [`scripts/scan_secrets.py`](../scripts/scan_secrets.py) runs locally and in CI (`.github/workflows/ci.yml`) to detect accidental `.env` commits, private keys, or provider token prefixes (`AIza...`, `sk-...`, `ghp_...`, `xoxb-...`, etc.).
- **Antigravity IDE Context-Exclusion Limitation:** `.gitignore` **does not** prevent AI coding assistants or IDE tools from reading untracked local files (such as `.env` or `secrets/*.json`). An Antigravity-specific ignore file mechanism (e.g., `.geminiignore` / `.antigravityignore`) could not be independently verified from local IDE configuration. **Safest owner practice:**
  1. Do not create real `.env` or OAuth credential files inside the workspace until the specific integration phase requires them.
  2. When credentials are needed in later phases, store master credential backups outside the workspace (e.g., Windows Credential Manager / password manager / encrypted host folder outside `C:\Media OS Agent`), never paste secrets into chat prompts, and avoid leaving real `.env` files open in IDE editor tabs.

---

## 2. Authentication & Session Protection

- **Dashboard Authentication (Phase 10):** The Next.js dashboard (`http://localhost:3000`) and FastAPI backend (`http://localhost:8000`) must enforce authenticated local sessions (e.g., `HttpOnly`, `SameSite=Lax` cookies or signed short-lived tokens) before exposing approval/publish controls—even on localhost—to guard against browser-based CSRF/DNS-rebinding attacks.
- **No Frontend Secret Exposure:** Server-only secrets (`GEMINI_API_KEY`, `POSTIZ_API_KEY`, `DATABASE_URL`) must **never** appear in `NEXT_PUBLIC_*` environment variables or client-side JavaScript bundles. Next.js Server Components/Route Handlers or FastAPI backend endpoints proxy all privileged operations.
- **MCP Access Control (Phase 9):** External AI agents connecting to `services/mcp` must authenticate with scoped bearer tokens and operate under least-privilege tool permissions; external MCP callers must not bypass human approval gates for YouTube uploads or billing actions.

---

## 3. OAuth Credential Ownership

| Credential / Token | Owning System | Storage & Isolation Rule |
|---|---|---|
| **Instagram / Meta OAuth & Page Tokens** | **Postiz** | Managed inside Postiz (`http://localhost:4007`) and stored in Postiz's PostgreSQL database. Media Intelligence OS never touches Meta OAuth secrets or Postiz DB. |
| **YouTube OAuth Tokens** | **Postiz** | Managed inside Postiz. Media Intelligence OS triggers uploads/drafts solely via Postiz API/MCP using `POSTIZ_API_KEY`. |
| **Postiz Public API Key** | **Media Intelligence OS Backend** | Phase 2 test key lives outside the Git repo in `C:\Media OS Agent\postiz-runtime\.api.env` (ACL-locked to owner & `SYSTEM`). In Phase 3+, stored strictly in backend `.env` (`POSTIZ_API_KEY`), never sent to `apps/web` browser client. |
| **Postiz Runtime DB & JWT Secrets** | **Postiz (`postiz-runtime`)** | Generated cryptographically locally into `C:\Media OS Agent\postiz-runtime\.env` (ACL-locked via `icacls` to owner & `SYSTEM`), excluded from `media-intelligence-os` Git repo, never printed in logs. |
| **Gemini Developer API Key** | **Media Intelligence OS Backend / Creative Worker** | Stored strictly in backend/worker `.env` (`GEMINI_API_KEY`), isolated inside the centralized Gemini provider adapter. |

### Postiz v2.25.3 Registration & Interface Auth Boundaries (Verified Phase 2)
- **Public Registration Locked (`POSTIZ_DISABLE_REGISTRATION=true`):** Verified via `GET /api/auth/can-register` (`{"register":false}`), `POST /api/auth/register` rejection (`HTTP 400 Registration is disabled`), `/auth` redirect to `/auth/login`, and active owner `LOCAL` login (`POST /api/auth/login`). Documented Postiz side effect: `DISABLE_REGISTRATION=true` also disables OIDC/OAuth sign-in.
- **Public REST API vs. MCP Auth Distinction:**
  - `/api/public/v1/*` (`PublicAuthMiddleware`) requires raw `Authorization: <POSTIZ_API_KEY>` (rejects `Bearer <key>` with `HTTP 401`).
  - `/api/mcp` (`startMcp`) accepts `Authorization: Bearer <POSTIZ_API_KEY>` (and `/mcp` without `/api` redirects to `/auth` on the frontend).
- **Loopback Isolation:** `postiz-runtime` exposes **only** `127.0.0.1:4007:5000`; PostgreSQL (`5432`), Redis (`6379`), Elasticsearch (`9200`), Temporal (`7233`), and Temporal UI (`8080`) have zero host port bindings.

---

## 4. Database & Volume Protection

- **Separate Databases:** Media Intelligence OS PostgreSQL (`media-os-postgres` / DB `media_os`) and Postiz PostgreSQL (`postiz-runtime-postgres`) run in isolated containers, separate Docker networks (`media-os-net`), and separate Docker volumes (`media_os_pgdata`, `media_os_redisdata`) with distinct credentials.
- **No Exposed DB/Broker Ports:** `compose.app.yml` keeps PostgreSQL (`5432`) and Redis (`6379`) strictly internal to `media-os-net` (`expose:` only, zero host `ports:`). Both services require explicit non-empty passwords (`${POSTGRES_PASSWORD:?...}`, `${REDIS_PASSWORD:?...}`). Redis authenticates via `requirepass` written to rootless `tmpfs` (`/tmp/redis-auth.conf`, mode `0600`).
- **No Plaintext Credentials in Logs or Payloads:** Structured log/audit formatters scrub `postgresql+psycopg://user:pass@`, `redis://:pass@`, DSN variables (`DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), cookies, CSRF headers, and Argon2id hashes (`observability/redaction.py`). Durable task payloads are validated by `validate_safe_task_payload()` before database persistence.

---

## 5. Network Exposure Rules

- **Loopback-Only Binding (`127.0.0.1`):** All host-exposed ports for Media Intelligence OS (`127.0.0.1:8000`, `127.0.0.1:3000`) must explicitly bind to `127.0.0.1`, never `0.0.0.0`, so services are unreachable from external LAN/Wi-Fi devices.
- **SSRF Prevention:** Outbound URL fetching in research connectors and media downloaders must validate schemes (`https://` only), reject private/loopback/link-local IP ranges, and enforce strict response size & timeout caps.
- **No Public URL Assumptions for Localhost:** Never pass `http://localhost:...` or `http://127.0.0.1:...` media URLs to Meta's Instagram Graph API; Instagram requires public HTTPS URLs (to be evaluated with owner approval in Phase 8).

---

## 6. Gemini API Key Handling & Quota Compliance

- **Centralized Provider Adapter (Phase 7):** All Gemini text and TTS calls pass through one audited module enforcing token accounting, cost caps, exponential backoff, error classification, and a circuit breaker.
- **Strict Non-Evasion Policy:**
  - **NEVER** rotate across multiple Gemini/Google accounts or keys to evade HTTP 429 quota limits.
  - **NEVER** bypass content safety refusals by switching credentials or obfuscating prompts.
  - **NEVER** include API keys inside prompt templates or debug logs.

---

## 7. Prompt-Injection Defense

All external research inputs (Hacker News titles/comments, GitHub READMEs/issues, Hugging Face model cards, arXiv abstracts, RSS feeds, YouTube transcripts/descriptions) are **untrusted data**:
1. **Strict Delimitation:** Prompts must wrap fetched source text in explicit data delimiters and instruct Gemini to treat it solely as text to summarize/analyze, never as executable instructions.
2. **Schema-Validated Outputs:** Model responses must parse through strict Pydantic schemas; unexpected tool calls, markdown injection, or chain-of-thought leaks (as observed in SocialAI's `BROKEN_CONTENT_PATTERNS`) are rejected by QA.
3. **Zero Implicit Execution:** Research content can never trigger shell commands, file writes, API key reads, source status changes, or social publishing without deterministic backend policy & human approval gates.

---

## 8. Audit Logging & Local Access Controls

- **Audit Logging & Structured JSON Tracing (Phase 3–4):**
  - Every HTTP request carries a sanitized correlation ID (`X-Request-ID`) and structured JSON log line (`request_id`, `method`, `route`, `status_code`, `duration_ms`) with secret scrubbing (`observability/redaction.py`).
  - Phase 4 adds durable PostgreSQL `audit_events` (`before_hash`/`after_hash`, secret-scrubbed metadata) exposed read-only via `GET /api/v1/system/audit-events`.
- **Persistent Single-Owner Authentication & Revocation (Phase 4):**
  - `OwnerUser` provisions once from configured `OWNER_USERNAME` and `OWNER_PASSWORD_HASH` / `OWNER_PASSWORD_HASH_B64`; subsequent API restarts **never** overwrite an existing database owner password hash.
  - Deliberate local password rotation runs via `python -m media_os_api.auth.rotate_password --username <owner>`, increments `token_version`, revokes all active `AuthSession` records, writes an `OWNER_PASSWORD_ROTATED` audit event, and refuses `--env-file` paths inside the Git repository.
  - Signed `HttpOnly` + `SameSite=Lax` cookies (`sub`, `jti`, `iat`, `exp`, `token_version`) are cross-checked on every authenticated request against PostgreSQL `AuthSession` (`revoked_at`, `expires_at`, `OwnerUser.is_active`, `OwnerUser.token_version`), preserving logout invalidation across API/DB restarts without storing raw session cookies.
  - Expired records are pruned safely via `cleanup_expired_auth_sessions()`; unexpired revoked `jti` rows are never deleted early.
  - Login brute-force protection uses `RedisLoginRateLimiter` (`media_os:rl:login:*`) persisting across API restarts, with configurable outage policy (`RATE_LIMIT_FAIL_CLOSED_ON_REDIS_OUTAGE=false` mirrored fallback vs `true` HTTP `503`).
- **Least-Privilege Containers:** `media-os-api`, `media-os-web`, `media-os-worker`, and `media-os-scheduler` run as non-root users with `read_only: true`, `tmpfs: [/tmp]`, `no-new-privileges:true`, and strict internal/loopback boundaries.

---

## 9. Backup Security, Incident Response & Credential Revocation

- **Backup Security & Isolated Verification:** PostgreSQL custom-format dumps (`*.dump`, `*.dump.sha256`) created by `scripts/backup_db.py` are stored outside Git (`C:\Media OS Agent\media-os-backups\`), excluded by `.gitignore`, verified via `scripts/restore_verify.py` in a disposable temporary database, and should be encrypted if copied to external/cloud storage.
- **Incident Response Playbook:**
  1. **Halt Execution:** Immediately stop custom workers (`docker compose stop`) or flip global pause flag in Media OS dashboard/API.
  2. **Contain Exposure:** Disable affected source connector (`BLOCKED`) or external MCP token.
  3. **Preserve Forensics:** Export container logs and PostgreSQL audit trail without destroying volumes.
  4. **Rotate & Revoke:** Follow Section 9.1 below before restarting services.
- **Credential Revocation Steps:**
  - *Gemini API Key leaked:* Delete/revoke key immediately in Google AI Studio / Google Cloud Console; generate replacement only when ready to resume; audit billing/usage logs.
  - *Postiz API Key leaked:* Rotate API key in Postiz Settings (`http://localhost:4007`) and update local `.env`.
  - *Social OAuth token compromised:* Revoke app authorization directly in Meta Business Integrations / Instagram Settings or Google Account Security (`myaccount.google.com/permissions`), then disconnect in Postiz.
  - *Git history leak:* Immediately revoke/rotate the exposed credential (Git history purge alone is insufficient once pushed or copied).

---

## 10. Multi-Brand Isolation & Cross-Tenant Safeguards

- **Shared Intelligence Decoupling:** Global candidate ingestion (`source_items`, `topic_clusters`) contains zero brand identity. Brand editorial interest is established strictly via `BrandTopicAssignment`.
- **Database Composite Foreign Keys:** Cross-brand leakage is prevented at the relational engine level:
  - `fk_research_packets_brand_topic (brand_profile_id, topic_cluster_id)` references `brand_topic_assignments (brand_profile_id, topic_cluster_id) ON DELETE RESTRICT`. A research packet cannot be inserted or reassigned to a brand profile without an explicit, matching brand topic assignment.
  - `uq_channel_accounts_id_brand (id, brand_profile_id)` and `uq_content_packages_id_brand (id, brand_profile_id)` prevent mixing channel accounts across different brands.
- **Service-Layer Enforcement:**
  - `create_research_packet_scoped()` enforces that a topic's `editorial_status` must be `APPROVED_FOR_RESEARCH` or `SHORTLISTED`. Attempting to generate research from `REJECTED`, `EXPIRED`, or `PENDING_EVALUATION` topics raises `BrandPolicyError`.
  - Reassigning content packages across brands via `reassign_content_package_brand()` is blocked once packages have associated assets or schedules.
  - Social posting adapters verify that `ContentPackage.brand_profile_id == ChannelAccount.brand_profile_id` before delegating to Postiz, preventing accidental cross-account mis-posting.
