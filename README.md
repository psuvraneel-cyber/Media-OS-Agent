# 🎬 Media OS Agent

> **⚠️ Work in Progress** — This project is actively under development. Features, APIs, and architecture are subject to change without notice.

A personal, local-first autonomous AI research, content-production, and social-media publishing intelligence platform. Media OS Agent orchestrates AI-driven workflows for media research, content generation, multi-brand social scheduling, and publishing — all running independently in Docker containers.

---

## 🚧 Project Status

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 0 | Project Contract & Governance | ✅ Complete |
| Phase 1 | Workspace Baseline & Docker Foundation | ✅ Complete |
| Phase 2 | Standalone Postiz v2.25.3 Integration | ✅ Complete |
| Phase 3 | Next.js Dashboard + FastAPI Control-Plane Skeleton | ✅ Complete |
| Phase 4 | PostgreSQL, Redis, Multi-Brand Schema, Durable Jobs, Outbox, Scheduler & Workflow State Machine | ✅ Complete |
| Phase 5 | Research Ingestion & Normalization Connectors | 🔜 Pending Authorization |
| Phase 6+ | Content Generation, QA, Approval Gates, Publishing | 🔜 Planned |

---

## 🏗️ Repository Structure

```text
Media OS Agent/
├── media-intelligence-os/   # Core monorepo: API, Web UI, workers, infra, tests, docs
│   ├── apps/
│   │   ├── web/             # Next.js 16 + TypeScript dark-first control plane & BFF proxy
│   │   └── api/             # Python 3.12 FastAPI + SQLAlchemy 2.x + Alembic + Celery
│   ├── workers/
│   │   ├── research/        # Research ingestion workers (Phase 5)
│   │   └── creative/        # Carousel & FFmpeg Shorts workers (Phase 7)
│   ├── services/
│   │   └── mcp/             # Controlled external MCP server (Phase 10)
│   ├── infra/
│   │   └── docker/          # Docker Compose specs
│   ├── docs/                # PRD, Architecture, Decisions, Operations, Phase specs
│   ├── tests/               # Unit, integration, fixture, and smoke tests
│   ├── scripts/             # Utility scripts (secret scanner, DB backup/restore)
│   └── .github/workflows/   # CI pipeline with PostgreSQL 17 & Redis 7.2
└── README.md                # This file
```

---

## ⚙️ Core Architecture Principles

1. **Strict Separation from Postiz** — Media Intelligence OS owns research, verification, content generation, rendering, QA, and approval state. [Postiz](https://github.com/gitroomhq/postiz-app) runs in its own separate Docker Compose stack and communicates solely via Public API / MCP boundaries.
2. **SocialAI Agent is Reference-Only** — Patterns from [SocialAI Agent](https://github.com/pavelblank/socialai-agent) are studied selectively. Its runtime components are **not** used directly.
3. **Human Approval Safety Gates** — Instagram starts in draft/review mode; YouTube requires explicit owner approval before any upload.
4. **Source Governance** — Reddit remains `BLOCKED` pending Data API Terms review. Only official APIs, RSS/Atom feeds, or permitted official monitoring are allowed.
5. **Zero Unauthorized Spend** — Zero paid APIs or paid hosting unless explicitly approved.

---

## 🚀 Getting Started

> **Note:** Detailed setup instructions are available in the [`media-intelligence-os/`](./media-intelligence-os/) subdirectory.

### Prerequisites

- Docker Desktop (latest)
- Python 3.12+
- Node.js 20+
- Git

### Quick Start

```bash
# Clone the repository
git clone https://github.com/psuvraneel-cyber/Media-OS-Agent.git
cd Media-OS-Agent

# See the core project README for full setup
cd media-intelligence-os
cat README.md
```

For Docker Compose setup, local Python environment, and phase-specific operations, refer to:
- [`media-intelligence-os/CONTRIBUTING.md`](./media-intelligence-os/CONTRIBUTING.md)
- [`media-intelligence-os/infra/docker/`](./media-intelligence-os/infra/docker/)
- [`media-intelligence-os/docs/`](./media-intelligence-os/docs/)

---

## 🔒 Security & Secrets

- **Never commit `.env` files** — use `.env.example` as a template
- All secrets are managed via environment variables injected at runtime
- Database dumps and runtime credential files are excluded from version control

---

## 📄 License

This project is currently **unlicensed** (all rights reserved). A license will be chosen before the first stable release.

---

## 🤝 Contributing

This is a personal project and not currently accepting external contributions. The roadmap and architecture decisions are documented in [`media-intelligence-os/docs/`](./media-intelligence-os/docs/).

---

*Built with ❤️ using FastAPI, Next.js, PostgreSQL, Redis, Celery, Docker, and AI-assisted development workflows.*
