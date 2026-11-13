# Product Requirements Document (PRD) — Media Intelligence OS

**Document Status:** Phase 4 Persistence Update (PostgreSQL Domain Schema, Multi-Brand Isolation & Workflow State Machine Live; Content/OAuth Defaults Pending Owner Confirmation in Later Phases)
**Last Updated:** 2026-11-10

---

## 1. Product Vision

**Media Intelligence OS** is a personal, local-first autonomous AI research, content-production, and multi-brand social-media publishing intelligence platform. Running inside Docker containers on the owner's workstation independently of Antigravity, it discovers verified technical developments and product/domain opportunities through a shared intelligence engine, transforms selected topics into brand-specific Instagram carousels and narrated YouTube Shorts across multiple independent `BrandProfile` identities and `ChannelAccount` destinations, enforces factual/editorial/visual quality gates, learns from per-channel performance signals, and delegates social-account OAuth and publishing exclusively to a separate Postiz instance.

---

## 2. Problem Statement

General-purpose social automation scripts (such as single-script prototype agents) suffer from fragile local state, lack multi-stage factual verification, mix publishing credentials with generation logic, rely on heavy local LLMs unsuitable for 16 GB laptops, and lack proper Instagram carousel/public-media support or human-in-the-loop safety gates. Conversely, standalone social schedulers (such as Postiz) handle platform OAuth and scheduling well, but do not provide deep technical research intelligence, source provenance, claim verification, structured creative rendering, or domain-tuned editorial quality assurance.

Media Intelligence OS bridges this gap cleanly by separating **Intelligence & Creative Production** (our custom system) from **Social Publishing & OAuth** (separate Postiz service).

---

## 3. Target User

- **Primary User:** Solo developer / AI & software engineering content creator running locally on a Windows 11 laptop (`Intel Core i5-13420H`, `16 GB DDR5 RAM`, integrated Intel graphics) with Docker Desktop.
- **Secondary Consumers:** Authorized external AI agents connecting via our controlled MCP interface (read/propose/queue within strict authorization policies).

---

## 4. Multi-Brand Scope, Initial Brand Profiles & Provisional Defaults

### 4.1 Multi-Brand & Multi-Channel Product Requirement (`ACCEPTED`)
Media Intelligence OS supports **multiple independent brand profiles (`BrandProfile`)** and **multiple connected social destination accounts (`ChannelAccount`)** rather than a single personal brand/account:
- **Brand Profile 1: Technology and AI (`brand-tech-ai`, seeded in Phase 4)** — AI developments and announcements, AI models and research, developer tools, open-source projects, GitHub discoveries, software engineering, and technical tutorials/explainers.
- **Brand Profile 2: App Promotion (`brand-app-promo`, seeded unconfigured in Phase 4)** — Promotion of the owner's published application, feature announcements, product demonstrations, tutorials, user education, release updates, and marketing content. *(Application name, exact feature set, and visual branding remain unconfigured until supplied by the owner; nothing is assumed or invented.)*
- **Shared Intelligence, Isolated Brand Execution:** Research discovery and primary-source verification operate as a shared pool, while editorial selection, prompts, visual templates, tone, CTAs, publishing schedules, content history, analytics, and approval settings remain strictly isolated per brand and per channel account with PostgreSQL composite-FK and service-layer cross-account mis-posting safeguards.

### 4.2 Provisional Content & Cadence Defaults

> [!NOTE]
> The content defaults below are **provisional** and remain marked `OWNER_CONFIRMATION_REQUIRED` until explicitly approved by the project owner.

| Attribute | Provisional Default | Approval Status |
|---|---|---|
| **Target Platforms (v1)** | Multiple Instagram Professional accounts (mapped to Brand Profiles 1 & 2); YouTube (optional multi-channel expansion) | `OWNER_CONFIRMATION_REQUIRED` |
| **Brand Profile 1 Niche** | Technology and AI (AI news, models, research, dev tools, open source, GitHub, software engineering, tutorials/explainers) | `OWNER_CONFIRMATION_REQUIRED` |
| **Brand Profile 2 Niche** | App Promotion (owner's published app announcements, demos, tutorials, education, release updates, marketing; app identity unconfigured) | `OWNER_CONFIRMATION_REQUIRED` |
| **Primary Language** | English (configurable per `BrandProfile`) | `OWNER_CONFIRMATION_REQUIRED` |
| **Editorial Voice** | Brand-specific (`BrandProfile.tone_of_voice`; technically credible/analytical baseline for Tech & AI) | `OWNER_CONFIRMATION_REQUIRED` |
| **Content Formats (v1)** | Instagram multi-slide carousels (HTML/CSS/SVG/Pillow/Playwright); Vertical narrated Shorts/Reels (Gemini TTS + FFmpeg) | `OWNER_CONFIRMATION_REQUIRED` |
| **Initial Cadence — Instagram** | Per-channel independent schedule (provisional baseline: 1 high-quality carousel/day on Tech & AI; **draft/review mode**) | `OWNER_CONFIRMATION_REQUIRED` |
| **Initial Cadence — YouTube** | Per-channel independent schedule (provisional baseline: 2–3 narrated Shorts/week; **explicit human approval** required) | `OWNER_CONFIRMATION_REQUIRED` |
| **Release Philosophy** | Quality and reliability strictly prioritized over posting volume | Accepted baseline |
| **Budget Constraint** | Zero paid APIs and zero paid hosting unless explicitly approved by owner | Accepted baseline |

### 4.3 Multi-Brand Domain Entities (Persisted in PostgreSQL in Phase 4 & Pre-Phase 5)
- **`BrandProfile` (`brand_profiles`):** `id`, `name`, `slug`, `description`, `niche`, `target_audience`, `language`, `tone_of_voice`, `content_pillars`, `visual_identity` (JSONB), `logo_asset_id` (nullable, constrained to same brand), `caption_preferences`, `hashtag_preferences`, `call_to_action_preferences`, `timezone`, `publishing_preferences` (JSONB), `is_active`.
- **`ChannelAccount` (`channel_accounts`):** Links a `BrandProfile` to an external Postiz integration/platform account (`INSTAGRAM`, `YOUTUBE`) storing only non-secret connection metadata; Postiz retains OAuth/refresh credentials.
- **`BrandTopicAssignment` (`brand_topic_assignments`):** Bridges globally discovered `TopicCluster` candidates to specific `BrandProfile` identities, persisting `editorial_status` (`PENDING_EVALUATION`, `CONSIDERED`, `SHORTLISTED`, `APPROVED_FOR_RESEARCH`, `REJECTED`, `EXPIRED`), bounded `relevance_score` (`0.0000 <= score <= 1.0000`, NULL default for unranked items), `score_breakdown` (JSONB), and review timestamps.
- **Attribution & Gating Rules:**
  - Every `ContentPackage`, `Asset`, `PublishSchedule`, and `MetricsSnapshot` is enforced by PostgreSQL composite foreign keys `(id, brand_profile_id)` and service-layer guards to belong strictly to one `BrandProfile`.
  - Every `ResearchPacket` is bound to an active `BrandTopicAssignment` via composite foreign key `fk_research_packets_brand_topic (brand_profile_id, topic_cluster_id)` and requires `APPROVED_FOR_RESEARCH` or `SHORTLISTED` editorial status.

---

## 5. Functional Requirements

| ID | Requirement | Description |
|---|---|---|
| **FR-01** | Approved Topic Discovery | Discover candidate stories exclusively from enabled, policy-compliant sources in the Source Registry (official APIs, RSS/Atom feeds, permitted low-frequency official monitoring). |
| **FR-02** | Normalization & Deduplication | Normalize titles/URLs/summaries and prevent duplicate or near-duplicate topic production across historical runs and queued candidates. |
| **FR-03** | Topic Ranking | Score and rank topics using source credibility weights, timeliness, niche relevance, and (once statistically valid) historical performance multipliers. |
| **FR-04** | Primary Source Research & Verification | Fetch primary source artifacts (paper abstract, release notes, repo README/changelog) and construct a structured Research Packet verifying factual claims before scripting. |
| **FR-05** | Editorial Intelligence Selection | Filter out hype, policy violations, unverified claims, and repetitive angles; select topics aligned with niche, voice, and calendar balance. |
| **FR-06** | Structured Content Generation | Generate structured JSON scripts, carousel slide outlines, captions, hooks, and metadata via a centralized Gemini Developer API adapter. |
| **FR-07** | Instagram Carousel Rendering | Render multi-slide high-resolution carousel assets using deterministic templates (HTML/CSS/SVG/Pillow/Playwright). |
| **FR-08** | Narrated Short Video Production | Generate voiceover narration using Gemini TTS and assemble vertical `9:16` Shorts with captions, visual slides/motion, and audio mixing using FFmpeg. |
| **FR-09** | Automated Quality Assurance (QA) | Run factual-grounding checks, editorial/safety checks, and visual/media validation (resolution, duration, legibility, missing assets, broken model text) prior to queue approval. |
| **FR-10** | Content Calendar & Approval Queue | Maintain persistent PostgreSQL-backed production queue, calendar slots, and 19 validated workflow lifecycle states (`DISCOVERED`, `NORMALIZED`, `DEDUPED`, `RANKED`, `RESEARCHED`, `DRAFTED`, `RENDERED`, `QA_PASSED`, `POSTIZ_DRAFT`, `USER_APPROVED`, `SCHEDULED`, `PUBLISHING`, `PUBLISHED`, `METRICS_CAPTURED`, `ARCHIVED`, `FAILED_RETRYABLE`, `FAILED_FINAL`, `REJECTED`, `PAUSED`) with audited transition history. |
| **FR-11** | Postiz Publishing & Analytics Integration | Interface with the standalone Postiz instance solely via supported Public REST API or MCP interfaces for media upload, draft/post scheduling, publishing, and supported analytics retrieval. |
| **FR-12** | Performance Learning Loop | Ingest authorized post metrics via Postiz and apply bounded, minimum-sample-gated adjustments to future topic category weights without suppressing manual owner topics. |
| **FR-13** | Controlled External MCP Server | Expose scoped MCP tools for external AI agents with explicit RBAC/policy enforcement so unapproved external agents cannot bypass human publishing gates. |
| **FR-14** | Standalone Local Dashboard | Provide a Next.js + TypeScript web dashboard (`http://localhost:3000`) independent of Antigravity for reviewing research packets, previewing carousels/videos, approving posts, managing brands/channels/sources, and inspecting worker/budget status. |
| **FR-15** | Multi-Brand & Multi-Channel Profiles | Support multiple independent `BrandProfile` configurations (starting with Technology & AI and App Promotion) mapped to multiple `ChannelAccount` destinations across Instagram and YouTube with independent schedules, calendars, templates, and analytics. |
| **FR-16** | Brand-Isolated Execution & Cross-Account Safeguards | Share research intelligence while executing brand-specific editorial selection and generation; enforce database-level composite-FK and application validation guaranteeing content for one brand/Instagram account cannot be published to another. |

---

## 6. Autonomy vs. Human Authorization Matrix

A strict boundary separates **autonomous background work** from **human-authorized external actions**:

### 6.1 Autonomous Operations (No Per-Action Human Prompt Required Once Configured)
- Scheduled discovery from `ENABLED` (or sandboxed `EXPERIMENTAL`) research sources.
- Story normalization, deduplication, and ranking.
- Primary-source fetching and Research Packet compilation.
- Draft copy, carousel slide, and video/Short asset generation (within configured daily Gemini token/cost caps).
- Factual, editorial, and visual quality checks.
- Retrieving read-only analytics for already-connected channels via Postiz.

### 6.2 Operations Requiring Explicit Human Authorization
| Action | Policy Requirement |
|---|---|
| **Enabling paid services / billing** | **Mandatory explicit owner approval** before enabling Google Cloud billing, paid Gemini tiers, Cloudflare R2 paid tiers, domains, or any third-party subscription. |
| **Granting OAuth permissions & connecting social accounts** | Owner manually performs OAuth consent flows inside Postiz (`http://localhost:4007`). |
| **Instagram publishing (initial release)** | Must operate in **draft/review mode** initially; automated blind posting is disabled until owner validates quality and approves live scheduling. |
| **YouTube uploads & visibility changes** | **Mandatory explicit approval** before each upload—or an explicitly approved batch—specifying the exact video artifact, target channel, title/description/tags metadata, and visibility (`private`/`unlisted`/`public`). |
| **Enabling `BLOCKED` or `EXPERIMENTAL` sources** | Owner confirmation required to promote any `BLOCKED` source (specifically Reddit) or rely on `EXPERIMENTAL` sources for production queues. |
| **Account permissions & cost-incurring changes** | Explicit owner confirmation required for scope expansion, budget cap increases, or public media bucket configuration. |

---

## 7. Source Registry & Source Governance Policy

### 7.1 Source Lifecycle Statuses
Every source connector in Media Intelligence OS must explicitly carry one of four runtime statuses:
- **`OFF`**: Disabled; connector does not execute.
- **`EXPERIMENTAL`**: Low-volume validation/testing only; output flagged and excluded from auto-selection unless explicitly allowed.
- **`ENABLED`**: Approved, active discovery source using permitted mechanisms.
- **`BLOCKED`**: Prohibited from execution due to legal, Terms of Service, or technical compliance restrictions.

### 7.2 Initial Source Registry

| Source | Planned Tier | Initial Runtime Status | Permitted Access Mechanism | Governance Notes |
|---|---|---|---|---|
| **Hacker News** | Planned | `OFF` (until Phase 5) | Official Firebase Hacker News API / Algolia HN Search API | High-signal AI/dev discussions |
| **GitHub** | Planned | `OFF` (until Phase 5) | Official GitHub REST/GraphQL API (releases, repos) | Authenticated/rate-limited official API only |
| **Hugging Face** | Planned | `OFF` (until Phase 5) | Official Hugging Face Hub API / Daily Papers API | Models, datasets, papers |
| **arXiv** | Planned | `OFF` (until Phase 5) | Official arXiv API / RSS (`cs.AI`, `cs.LG`, `cs.CL`, `cs.SE`) | Respect arXiv rate limits (≥3s spacing) |
| **OpenAI News** | Planned | `OFF` (until Phase 5) | Official RSS/Atom feed or permitted low-frequency official page check | Primary vendor announcements |
| **Anthropic News** | Planned | `OFF` (until Phase 5) | Official RSS/Atom or permitted low-frequency official page check | Primary vendor announcements |
| **Meta AI Blog** | Planned | `OFF` (until Phase 5) | Official RSS/Atom or permitted low-frequency official page check | Open-weights & research releases |
| **Google Trends** | Experimental | `EXPERIMENTAL` (until Phase 5 validation) | Official RSS feed (`trends.google.com/trending/rss`) | Broad consumer pulse; noisy for dev niche |
| **YouTube Public Metadata** | Planned | `OFF` (until Phase 5) | Official YouTube Data API v3 (metadata/captions) or permitted metadata checks | Comply strictly with YouTube API ToS |
| **Reddit** | **BLOCKED** | **`BLOCKED`** | None permitted | **Must remain BLOCKED** until current Reddit Data API Terms and our exact non-commercial/personal use case are formally reviewed and approved by owner. |

### 7.3 Anti-Scraping & Untrusted Input Rules
1. **No General-Purpose Scraper:** Media Intelligence OS will never ship a generic HTML web scraper or residential-proxy crawler. Connectors are restricted to official APIs, published RSS/Atom feeds, or explicitly permitted low-frequency official-page monitoring obeying `robots.txt` and `Retry-After` headers.
2. **Untrusted Content Isolation:** All fetched source text (titles, abstracts, comments, READMEs, transcripts) is **untrusted input**. Prompts must clearly delimit external source text as data; source content must never alter system instructions, invoke tools, override safety gates, or leak environment secrets.

---

## 8. Non-Functional Requirements

- **NFR-01 (Runtime Independence):** Production runtime executes entirely via Docker Compose on the local machine; Antigravity is used for development only.
- **NFR-02 (Service Isolation):** Media Intelligence OS and Postiz maintain separate Compose projects, PostgreSQL databases, Redis instances, networks, and persistent volumes.
- **NFR-03 (Laptop Hardware Compatibility):** Designed for `i5-13420H` / `16 GB DDR5` / integrated Intel GPU. Zero local LLMs (no Ollama). Creative worker concurrency fixed at `1`.
- **NFR-04 (Security & Privacy):** Zero secrets in source code, prompts, logs, Git, or frontend bundles. All services bind to `127.0.0.1` by default.
- **NFR-05 (Maintainability & Typed Contracts):** Strict type checking (`mypy` in Python, `tsc` in TypeScript), deterministic schema validation (Pydantic), automated linting/formatting, and CI validation.
- **NFR-06 (Observability & Auditability):** Structured JSON logging (scrubbed of secrets), full job/run state transitions in PostgreSQL, and immutable audit entries for approvals and publish attempts.
- **NFR-07 (Reproducibility):** Pinned runtime versions (Python `3.12`), pinned Compose baseline, and SHA-256 provenance tracking for read-only reference snapshots.

---

## 9. Failure-Handling Expectations

- **Gemini Quota / HTTP 429:** Pause generation via circuit breaker + exponential backoff; **never** rotate multiple Google/Gemini accounts or keys to evade rate limits.
- **Internet Outage:** Gracefully pause remote discovery/Gemini/Postiz jobs; preserve local queue state; surface clear degraded status on dashboard without corrupting jobs.
- **Postiz Unavailable:** Retain rendered artifacts and approved job state locally; retry idempotently only after health check passes; never fall back to direct DB writes or SocialAI's incomplete `meta.py`.
- **Laptop Sleep / Docker Restart:** Persist job state and checkpoint artifacts on durable volumes so interrupted creative jobs recover cleanly or fail deterministically without double-publishing.

---

## 10. Explicit Non-Goals (First Release)

- No local LLM inference (no Ollama, vLLM, or local Stable Diffusion models).
- No merging or forking of Postiz source code.
- No multi-user SaaS hosting, public internet SaaS exposure, or Stripe billing.
- No TikTok, X/Twitter, LinkedIn, or Reddit automated posting in v1.
- No fully autonomous unreviewed YouTube public uploads.
- No generic web-scraping engine or CAPTCHA/bot bypass mechanisms.
- No multi-account Gemini quota-evasion rotation.

---

## 11. First-Release Acceptance Criteria

1. Custom application operates end-to-end inside Docker independently of Antigravity while Postiz runs in its own isolated Compose stack.
2. Daily discovery ingests only from `ENABLED` official sources; Reddit stays `BLOCKED`.
3. Every generated Instagram carousel and YouTube Short passes factual, editorial, and visual QA checks and includes provenance links to primary sources.
4. Instagram publishing works via Postiz in draft/review mode (with owner-approved public HTTPS media delivery once Phase 8/hosting is approved).
5. YouTube Shorts require and enforce explicit human approval (video, channel, metadata, visibility) before upload via Postiz.
6. All unit, integration, Docker health, and secret-hygiene checks pass cleanly under laptop RAM limits (`16 GB` host).
