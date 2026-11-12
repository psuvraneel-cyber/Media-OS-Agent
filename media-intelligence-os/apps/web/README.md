# Web Dashboard (`apps/web`)

**Current Stage (Phase 4):** Next.js `16.4.6` (App Router, `output: "standalone"`) + React `19.3.1` + TypeScript `5.9.3` + Tailwind CSS `4.3.5` operator dashboard bound to `http://127.0.0.1:3000`.

Implements:
- Server-gated authenticated shell (`(shell)/layout.tsx`) with `/login` authentication flow and same-origin BFF proxy (`/api/v1/[...path]`) forwarding to server-only `INTERNAL_API_BASE_URL`
- Responsive dark-first sidebar + topbar + PostgreSQL-backed `BrandSwitcher` (`/api/v1/brands?include_inactive=true`: `brand-tech-ai` and unconfigured `brand-app-promo`)
- Live brand activation toggle (`/settings`) and persisted `AuditEvent` trail alongside ephemeral request telemetry (`/logs`)
- All 12 navigation views (`/dashboard`, `/research`, `/topics`, `/studio`, `/assets`, `/calendar`, `/approvals`, `/analytics`, `/agents`, `/integrations`, `/settings`, `/logs`) with honest Phase 5–10 disabled pipeline controls and zero fabricated analytics

**Checks (using `npm` + `package-lock.json`):**

```powershell
npm ci
npx next typegen
npm run typecheck
npm run lint
npm test
npm run build
```
