# Media Intelligence OS

A personal, local-first autonomous AI research, content-production, and social-media publishing intelligence platform running in Docker containers independently of Antigravity.

**Current Project Stage:** Phase 0 (Project Contract), Phase 1 (Workspace Baseline), Phase 2 (Standalone Postiz v2.25.3), Phase 3 (Next.js Dashboard + FastAPI Control-Plane Skeleton), and Phase 4 (PostgreSQL, Redis, Multi-Brand Schema, Durable Jobs, Outbox, Scheduler & Workflow State Machine) complete.
**Next Authorized Phase:** Phase 5 — Research Ingestion & Normalization Connectors (pending owner authorization).

---

## Core Architecture Principles

1. **Strict Separation from Postiz:** Media Intelligence OS owns research, verification, content generation, rendering, QA, and approval state. [Postiz](https://github.com/gitroomhq/postiz-app) (`v1.47.0`, AGPL-3.0) runs in its own separate Docker Compose stack and communicates solely via supported Public API / MCP boundaries.
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
│   ├── research/         # Future research ingestion workers (Phase 5)
│   └── creative/         # Future carousel & FFmpeg Shorts workers (Phase 7, concurrency=1)
├── services/
│   └── mcp/              # Future controlled external MCP server (Phase 10)
├── infra/
│   └── docker/           # Docker Compose specs (`compose.baseline.yml` Phase 1; `compose.app.yml` Phase 4)
├── docs/                 # PRD, Architecture, Decisions, Security, Operations, Environment, Phase 2–4 specs
├── tests/
│   ├── unit/             # Pure Python unit tests
│   ├── integration/      # Compose, PostgreSQL/Redis persistence, isolation, outbox, and restart tests
│   ├── fixtures/         # Deterministic test fixtures
│   └── smoke/            # Live workspace, secret-hygiene, and Docker baseline lifecycle tests
├── references/           # Read-only reference repository notes & SHA-256 provenance manifests
├── scripts/              # Utility scripts (secret scanner, reference manifests, DB backup & restore verify)
└── .github/workflows/    # CI validation pipeline (with PostgreSQL 17 & Redis 7.2 service containers)
```

---

## Quick Start (Phase 1 Baseline & Phase 4 Runtime)

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for local Python `3.12` setup, [`infra/docker/README.md`](./infra/docker/README.md) for Docker Compose topologies, and [`docs/PHASE_4_IMPLEMENTATION.md`](./docs/PHASE_4_IMPLEMENTATION.md) for verified Phase 4 persistence, backup/restore, and operations.
