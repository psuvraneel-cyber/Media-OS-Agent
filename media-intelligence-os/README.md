# Media Intelligence OS

A personal, local-first autonomous AI research, content-production, and social-media publishing intelligence platform running in Docker containers independently of Antigravity.

> ← Back to the [project overview](../README.md): features, status, quick start and releases.

**Current stage (v1.0):** Phases 0–5 plus the Nimontron track are complete and deployed:
- research ingestion (sources `OFF` until enabled);
- AI carousels with QA;
- scheduled Instagram and Facebook publishing;
- Studio for your own reels, photos and carousels;
- per-post and whole-account analytics;
- Telegram alerts and reports;
- the Windows self-healer.

See [`docs/OPERATIONS.md`](./docs/OPERATIONS.md).
**Next:** link accounts to brands and add a New brand form, YouTube Shorts and autopilot, per [`docs/ROADMAP.md`](./docs/ROADMAP.md).

---

## Core Architecture Principles

1. **Strict Separation from Postiz:** Media Intelligence OS owns research, verification, content generation, rendering, QA, and approval state. [Postiz](https://github.com/gitroomhq/postiz-app) (deployed `v2.25.3`, AGPL-3.0; the read-only reference archive is `v1.47.0`) runs in its own separate Docker Compose stack and communicates solely via supported Public API / MCP boundaries.
2. **SocialAI Agent is Reference-Only:** Patterns from [SocialAI Agent](https://github.com/pavelblank/socialai-agent) are studied selectively. Its SQLite DB, local Ollama runtime, Reddit scraper, and Facebook-only `meta.py` are **not** used.
3. **Human Approval Safety Gates:** Instagram starts in draft/review mode; YouTube requires explicit owner approval (artifact, channel, metadata, visibility) before upload.
4. **Source Governance:** Reddit remains **`BLOCKED`** pending Data API Terms review. Only official APIs, RSS/Atom feeds, or permitted low-frequency official monitoring are allowed.
5. **Zero Unauthorized Spend:** Zero paid APIs or paid hosting unless explicitly approved by the owner.

---

## Monorepo Layout

```text
media-intelligence-os/
├── apps/
│   ├── web/              # Next.js 16 + TypeScript dark-first control plane & BFF proxy
│   └── api/              # Python 3.12 FastAPI + SQLAlchemy 2.x + Alembic + Celery + Scheduler
├── workers/
│   ├── research/         # Research worker entry point (fetchers live in apps/api/.../research/)
│   └── creative/         # Future carousel & FFmpeg Shorts workers (Phase 7, concurrency=1)
├── services/
│   └── mcp/              # Future controlled external MCP server (Phase 10)
├── infra/
│   └── docker/           # Docker Compose specs (`compose.baseline.yml` Phase 1; `compose.app.yml` Phase 4)
├── docs/                 # PRD, Roadmap, Architecture, Decisions, Security, Operations, Phase 2–5 reports, brand briefs
├── tests/
│   ├── unit/             # Pure Python unit tests
│   ├── integration/      # Disposable-stack tests: persistence, isolation, outbox, research runner, API
│   ├── fixtures/         # Synthetic research fixtures (HN, arXiv, OpenAI RSS, GitHub, Hugging Face)
│   └── smoke/            # Live workspace, secret-hygiene, and Docker baseline lifecycle tests
├── references/           # Read-only reference repository notes & SHA-256 provenance manifests
├── scripts/              # Utility scripts (secret scanner, reference manifests, DB backup & restore verify)
└── (CI lives at the repository root: .github/workflows/ci.yml, PostgreSQL 17 & Redis 7.2 services)
```

---

## Quick Start (Phase 1 Baseline & Phase 4 Runtime)

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for local Python `3.12` setup, [`infra/docker/README.md`](./infra/docker/README.md) for Docker Compose topologies, and [`docs/PHASE_4_IMPLEMENTATION.md`](./docs/PHASE_4_IMPLEMENTATION.md) for verified Phase 4 persistence, backup/restore, and operations.
