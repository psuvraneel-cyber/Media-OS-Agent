# Postiz Standalone Deployment Guide (`v2.25.3`)

**Status:** Deployed & Verified (Phase 2)
**Last Updated:** 2026-11-10

---

## 1. Version, Provenance & Historical Snapshot Separation

| Artifact | Value | Notes |
|---|---|---|
| **Deployed Release** | `v2.25.3` (2026-10-24) | Patches security advisories `PSA-2026-E9AHFV` and `PSA-2026-YT2HMH` |
| **Upstream App Repo / Commit** | `https://github.com/gitroomhq/postiz-app` (`a5affe62c74e39c1e7ac960d50d8418b807d532c`) | Tag `v2.25.3` |
| **Official Compose Repo / Commit** | `https://github.com/gitroomhq/postiz-docker-compose` (`dd4969e5e694cd009619a0d53cff14c21104580b`) | 2026-07-30 |
| **Primary App Image** | `ghcr.io/gitroomhq/postiz-app:v2.25.3` | Multi-arch index digest below |
| **OCI Multi-Arch Index Digest** | `sha256:1e376ff2cb781022d528f171719cf174cf693a985a30cb40eefb13eae3ddda13` | `linux/amd64` manifest: `sha256:3e2facf0c1ffdfe07b31cebdc5b850767721cb40a7a389dd07485f8e381bb49f` |
| **Deployment Directory** | `C:\Media OS Agent\postiz-runtime` | Independent from `media-intelligence-os` |
| **Compose Project Name** | `postiz-runtime` | Isolated networks & named volumes |
| **Historical Reference Archive** | `C:\Media OS Agent\postiz-app-main` | `version.txt` = `v1.47.0`; kept 100% untouched & read-only |

---

## 2. Container Topology & Pinned Digests

All containers in `C:\Media OS Agent\postiz-runtime\docker-compose.yaml` are pinned by exact tag and OCI SHA-256 digest:

| Service | Container Name | Image & Pinned Digest | Healthcheck / Role | Host Ports |
|---|---|---|---|---|
| `postiz` | `postiz-runtime-app` | `ghcr.io/gitroomhq/postiz-app:v2.25.3@sha256:1e376ff2cb781022d528f171719cf174cf693a985a30cb40eefb13eae3ddda13` | HTTP `localhost:5000/` (`nginx` + Next.js `4200` + NestJS `3000`) | **`127.0.0.1:4007:5000`** |
| `postiz-postgres` | `postiz-runtime-postgres` | `postgres:17-alpine@sha256:3540c0362c809df084ffac0ae69fe794515f9de92692f02178bcdce0ef0302ac` | `pg_isready` (Postiz DB) | None (`expose: 5432`) |
| `postiz-redis` | `postiz-runtime-redis` | `redis:7.2@sha256:5b816bb37d9707a70f10ef5002ecf8ee431750d397010fc4314c93dca887962f` | `redis-cli ping` (Postiz Redis) | None (`expose: 6379`) |
| `temporal` | `postiz-runtime-temporal` | `temporalio/auto-setup:1.28.1@sha256:607d68caa111338d754771efb876c92dfcdae06d056e4530bb31cd0f37406e6a` | `temporal operator cluster health` | None (`expose: 7233`) |
| `temporal-postgresql` | `postiz-runtime-temporal-postgres` | `postgres:16@sha256:f9a7b9ebf6f56201f949db745e47bc423c0f0f8294bafe991cc10bf74b029657` | `pg_isready -U temporal` | None (`expose: 5432`) |
| `temporal-elasticsearch` | `postiz-runtime-temporal-elasticsearch` | `elasticsearch:7.17.27@sha256:9a6443f55243f6acbfeb4a112d15eb3b9aac74bf25e0e39fa19b3ddd3a6879d0` | `/_cluster/health` (`-Xms256m -Xmx256m`) | None (`expose: 9200`) |
| `temporal-admin-tools` | `postiz-runtime-temporal-admin-tools` | `temporalio/admin-tools:1.28.1-tctl-1.18.4-cli-1.4.1@sha256:01537b62d995f27a0f0d33a01ac4caa6779622f454fb2eb36fed0dccd45c6244` | `sleep infinity` admin CLI utility | None |
| `temporal-ui` | `postiz-runtime-temporal-ui` | `temporalio/ui:2.34.0@sha256:cb17ea423d76a8a19a269d0bcd81fc12eee1f6365acd2a56b590dafb35696a95` | `curl -f http://localhost:8080/healthz` | None (`expose: 8080`) |
| `spotlight` *(debug profile)* | `postiz-runtime-spotlight` | `ghcr.io/getsentry/spotlight@sha256:8c0db1d8ce11db8587b70bc1971cd731d0a71b37214de3ad322e28183f2e5a2c` | Disabled unless `--profile debug` | `127.0.0.1:8969` (debug only) |

- **Networks:** `postiz-runtime-network` and `postiz-runtime-temporal-network` (bridge).
- **Named Volumes:** `postiz-runtime-postgres-data`, `postiz-runtime-redis-data`, `postiz-runtime-config`, `postiz-runtime-uploads`, `postiz-runtime-temporal-postgres-data`, `postiz-runtime-temporal-es-data`.
- **Changes vs. `v1.47.0` Reference Archive (`postiz-app-main/docker-compose.yaml`):** Added `RUN_CRON: "true"`, explicit container healthchecks across Temporal/ES/Postgres/Postiz, named volumes for Temporal Postgres & Elasticsearch (replacing anonymous paths), `profiles: [debug]` on `spotlight`, and loopback-only host exposure (`127.0.0.1:4007:5000`, internal Temporal/ES/DB ports unexposed to host).

---

## 3. Environment & Security Configuration

- **Runtime Secrets (`C:\Media OS Agent\postiz-runtime\.env`):** Holds cryptographically random `POSTIZ_JWT_SECRET`, `POSTIZ_POSTGRES_PASSWORD`, `TEMPORAL_POSTGRES_PASSWORD`, and `POSTIZ_DISABLE_REGISTRATION=true`. Restricted via Windows ACL (`icacls`) to `SYSTEM` and the owner user only.
- **API Test Credentials (`C:\Media OS Agent\postiz-runtime\.api.env`):** Holds `POSTIZ_BASE_URL=http://localhost:4007` and `POSTIZ_API_KEY` outside `media-intelligence-os`, similarly ACL-locked.
- **Public Registration Lock (`DISABLE_REGISTRATION=true`):**
  - Enabled after single-owner signup. Verified via `GET /api/auth/can-register` → `{"register":false}`, `POST /api/auth/register` → `HTTP 400 Registration is disabled`, `/auth` UI redirecting to `/auth/login`, and `POST /api/auth/login` remaining active.
  - **Documented Side Effect:** Per Postiz docs and `auth.service.ts`, `DISABLE_REGISTRATION=true` also disables OIDC/OAuth sign-in for new/social accounts (`LOCAL` password login for the existing owner remains active).
- **Storage (`STORAGE_PROVIDER=local`):** Uploads write to `postiz-runtime-uploads` (`/uploads`). Localhost URLs cannot be fetched by Meta's Instagram Graph API; public HTTPS media storage (e.g., owner-approved Cloudflare R2) remains deferred until Phase 8.

---

## 4. Verified API & MCP Interfaces (`v2.25.3`)

1. **Web UI:** `http://localhost:4007` (loopback `127.0.0.1:4007`)
2. **Public REST API:** `http://localhost:4007/api/public/v1`
   - **Authentication:** Raw `Authorization: <POSTIZ_API_KEY>` header (`PublicAuthMiddleware` passes the header value directly to `getOrgByApiKey`). Sending `Bearer <key>` returns `HTTP 401 Invalid API key`.
   - **Verified Routes:** `GET /me`, `GET /is-connected`, `GET /integrations`, `GET /groups`, `GET /posts`, `GET /media`.
   - **Draft Behavior (`POST /api/public/v1/posts`):** Verified in `posts.service.ts` and live HTTP tests that `type: "draft"` requires at least one valid connected `integration.id` (`HTTP 400 All posts must have an integration id` when empty). No fake integrations were created.
   - **Rate Limit:** Configured `API_LIMIT=30` requests/hour on create-post.
3. **MCP Server:** `http://localhost:4007/api/mcp`
   - **Routing Caveat:** `http://localhost:4007/mcp` routes to Next.js (`4200`) and redirects `HTTP 307` to `/auth`. External clients must connect to **`http://localhost:4007/api/mcp`** (proxied by nginx `/api/` → NestJS `3000`).
   - **Transport & Auth:** Streamable HTTP (`text/event-stream`), `Authorization: Bearer <POSTIZ_API_KEY>` (also accepts raw key).
   - **Verified Tools (`tools/list`):** Discovered **20 tools** (`integrationList`, `groupList`, `integrationSchema`, `triggerTool`, `integrationSchedulePostTool`, `postsListTool`, `postSettingsTool`, `deletePostTool`, `generateVideoOptions`, `videoFunctionTool`, `generateVideoTool`, `videoStatusTool`, `generateImageTool`, `uploadFromUrlTool`, `mediaListTool`, `mediaStatusTool`, `uploadWidgetTool`, `uploadWidgetTicketTool`, `uploadWidgetStatusTool`, `ask_postiz`).

---

## 5. Lifecycle, Backup & Upgrade Commands

Run from `C:\Media OS Agent\postiz-runtime` (never use `down -v`):

```powershell
# Validate Compose syntax without printing secret values
docker compose config --quiet

# Start stack and wait for health checks
docker compose up -d --wait

# Wait for backend API readiness after restarting `postiz` (nginx/frontend boots slightly before NestJS)
Invoke-RestMethod -Uri "http://localhost:4007/api/auth/can-register"

# Restart single service
docker compose restart postiz

# Safe stop (preserves all volumes & data)
docker compose down
```

### Backup & Safe Upgrade Procedure
1. Back up `postiz-postgres` (`docker exec postiz-runtime-postgres pg_dump -U postiz-user -Fc postiz-db-local > backups\postiz_YYYYMMDD.dump`) and snapshot `.env` / `postiz-runtime-uploads`.
2. Verify any newer upstream `gitroomhq/postiz-app` release notes, advisories, and `postiz-docker-compose` schema changes before changing the pinned digest in `docker-compose.yaml`.
3. Pull new pinned digest (`docker compose pull`), run `docker compose up -d --wait`, and verify `/api/auth/can-register` and `pytest tests/integration/test_postiz_api_mcp.py`.
