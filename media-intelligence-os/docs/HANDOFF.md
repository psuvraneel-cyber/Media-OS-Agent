# Handoff: Media OS current state

**Updated:** 20 Nov 2026. Sections 1, 3 and 5 reflect the week-1 fixes branch
(`claude/week1-fixes`); the rest is from 15 Nov and still accurate unless noted.
**Owner goal:** market the Nimontron Android app on Instagram and Facebook, and later on YouTube.
Carousels are written by free-tier AI, checked automatically, approved by the owner, and
published through Postiz. Constraints:
- zero budget;
- every post approved by a human;
- Reddit is never used.

---

## 1. Where things stand

| Area | State |
|---|---|
| Phase 5 research ingestion | Built. 5 sources were live-verified; all are **OFF** and their schedules disabled. Reddit is **BLOCKED** at the database level. |
| N0 brand onboarding | Done. Nimontron profile, pillars, post-tag taxonomy, content strategy (`docs/brands/nimontron/`). |
| N1 free-tier AI router | Live. Gemini → Groq → OpenRouter (`:free`) → Cloudflare, with automatic failover. |
| N2 carousels → review | Live. Writer (BN / BN_EN / EN), design v2 renderer, text and render QA, score v0, review queue. |
| N3 publishing | Live. GitHub Pages image hosting → Postiz → Instagram and Facebook; status sync every 15 min. |
| Posting | Live since 15 Nov. By 19 Nov: 8 carousels and 2 owner reels published on Instagram and Facebook. |
| Results so far | Reels ≈1,800 Instagram views each; carousels ≈4; Facebook ≈0. Video is the priority. |
| Studio, autopilot, design templates | Live: own uploads, Generate button, daily drafts (08:00 IST), auto-scheduling, posts per day. |
| Review and publishing (20 Nov) | Edit text before approving; rejection notes and edits steer the writer; cancel or move a post from Media OS; daily clean-up of hosted media; cross-language repeat check; live process status on the Agents page. |

Next on the roadmap (`docs/ROADMAP.md`):
1. **Scheduled generation:** a daily job that drafts carousels from the content calendar into the review queue.
2. **N4:** Reels and Shorts.
3. **N5:** analytics and the learning loop.
4. **N6:** long-form YouTube.
5. **N7:** gradual autonomy.
6. **Track T:** the tech brand.

## 2. How to use it

- **Dashboard:** http://localhost:3100. It moved from 3000, which another local project uses. Log in as `local-owner`; reset the password with `docker exec -it media-os-api python -m media_os_api.auth.rotate_password --username local-owner`.
- **Approvals → Review:** see the slides, caption, QA checks and score, then approve or reject. Failed drafts show why and offer **Re-check with latest rules**.
- **Approvals → Approved — ready to post:** pick a time and channels, then click **Schedule**. Caps: 2 posts per platform per day, scheduled 20 min to 30 days ahead.
- **Generate a carousel (CLI, in the worker):**
  ```
  docker exec media-os-worker python -m media_os_api.carousel.admin generate --pillar wedding_season --hook PAIN_POINT --occasion WEDDING_MUSLIM --language BN_EN --visual CARD_SHOWCASE
  ```
  `draft` instead of `generate` previews without saving.
- **AI providers:**
  - `python -m media_os_api.ai.admin providers|models|ping` (run in the worker).
  - Each carousel uses about 2 Gemini Flash requests out of a free tier of about 20 per day; beyond that the router fails over automatically.
- **Postiz:** http://localhost:4007, a separate stack with 8 containers. Delete a scheduled post there to cancel it; the sync marks it CANCELLED.

## 3. Runtime

- **Media OS stack** (`infra/docker/compose.app.yml`, env `C:\Media OS Agent\media-os-runtime\.env`):
  - containers: `media-os-{api,worker,scheduler,web,creative,postgres,redis}`;
  - the database is at migration `0015_design_templates`; the week-1 fixes add
    `0016_media_cleanup` (one schedule row, no table changes), so back up and migrate on deploy;
  - rebuild and deploy with `docker compose --env-file "C:\Media OS Agent\media-os-runtime\.env" -f infra/docker/compose.app.yml build|up -d --no-deps <services>`;
  - migrate with `... run --rm --no-deps media-os-api alembic upgrade head`;
  - always back up first with `scripts/backup_db.py` and `scripts/restore_verify.py` (dumps go to `C:\Media OS Agent\media-os-backups`; latest `media_os_20261114T195350Z.dump`).
- **Containers and keys:**

  | Container | Role | AI keys | Publishing keys | Media volume |
  |---|---|---|---|---|
  | worker | AI calls and publishing (`control, maintenance, default, research` queues) | yes | yes | read-only |
  | creative | headless Chromium on the `creative` queue; reads `C:\nimontron\store` read-only | no | no | read/write |
  | api | — | no | no | read-only |
  | web | — | no | no | — |

- **Env keys in the runtime `.env`:**
  - AI: `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`;
  - publishing: `POSTIZ_API_KEY`, `NIMONTRON_MEDIA_GITHUB_TOKEN`, `NIMONTRON_MEDIA_GITHUB_REPO`, `NIMONTRON_MEDIA_BASE_URL`;
  - optional: `MEDIA_OS_WEB_PORT`, `MEDIA_OS_WEB_ORIGINS`;
  - `CORS_ALLOWED_ORIGINS` is no longer read and can be deleted.
- **Postiz:**
  - its env is `C:\Media OS Agent\postiz-runtime\.env`, which holds `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET` for the Meta app "Nimontron Publisher" (App ID `1862070358151146`, **Development mode**);
  - its compose file now reads those values from `.env`; a backup is in `postiz-runtime/backups/`;
  - channels connected: Instagram `@com.nimontron.app` and the Facebook Page, both "Nimontron: Bengali Invitations".
- **Image hosting:** public repo `psuvraneel-cyber/nimontron-media` on GitHub Pages, served at `https://psuvraneel-cyber.github.io/nimontron-media/`. It is separate from the legal site `nimontron-site`. The token is fine-grained, limited to that repo with Contents read/write, and expires in about a year.
- **Schedules:** `publish.sync_status`, `content.daily_drafts` and (after 0016)
  `publish.cleanup_media` are **enabled**; the 5 `research.fetch.*` schedules are disabled.
- **Rollback image tags:** `rollback-pre-n3`, `rollback-pre-n2-design`, `rollback-pre-n2`, `rollback-pre-n1` (api and web; creative has `rollback-pre-n2-design`).
- **Docker Desktop sometimes stops on its own.** Restart it from `C:\Users\Sauvraneel Paul\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`. The PC and Docker must be running at post times, because Postiz publishes locally.

## 4. Safety rules in force

- **Approval:** nothing publishes without owner approval plus an explicit Schedule action. Approving alone publishes nothing.
- **Claims:** content may only use claims from `carousel/brand_kit.py`, transcribed from the brand brief. QA blocks:
  - rankings and install numbers;
  - testimonials;
  - Christian and Buddhist cards (wording not yet approved);
  - iOS mentions;
  - India-only prices shown without saying so;
  - Devanagari text;
  - a premium design named next to a different card;
  - Tagore mentions are a warning only.
- **Assets:** never use `raw/card-2.png`, `screenshots/3.png` or `screenshots/4.png`. They credit the default wedding verse to Tagore, which CHANGELOG 1.1.0 says is wrong. If the same screenshots are on the Play listing, the owner should regenerate them.
- **Budget and credentials:**
  - no paid services;
  - no rotating multiple keys of one provider;
  - never print, log or commit secrets;
  - never read `.env` values (key-name checks only).
- **Git and runtime:**
  - branch, then PR, then green CI, then merge, then deploy;
  - never use `--no-verify`;
  - back up before any migration;
  - never run destructive Docker commands;
  - Postiz stays an independent stack.

## 5. Open items and known issues

1. **Carousels barely reach anyone** (≈4 views each vs ≈1,800 for reels). Next: N4 reels engine
   and N5 learning loop.
2. **More card renders wanted:** only two clean cards exist (Muslim wedding, annaprashan). Exports from the app (Hindu wedding, gaye holud, a premium design, puja) would vary the posts. Suggested folder: `C:\nimontron\store\marketing\`.
3. **Old-design approved carousels:** `3a71311d` and `13e99ba8` can be posted, or regenerated in design v2.
4. **Creative image size:** 3.76 GB because the Playwright base ships three browsers; it can be trimmed.
5. ~~Dashboard hook text showed `*emphasis*` asterisks~~: fixed 20 Nov (new posts store plain
   titles; older ones are shown without the markers).
6. **Phase 5 follow-ups:** GitHub new-repo search mode, a Hugging Face brand-safety filter, ranking OpenAI case studies lower, persisting source metrics.
7. **Local hook quirk:** the pre-commit manifest hook needs `MEDIA_OS_WORKSPACE_ROOT="C:/Media OS Agent"` when committing from a worktree.

## 6. Key docs

`ROADMAP.md`, `N2_CAROUSEL_PIPELINE.md`, `N3_PUBLISHING.md`, `PHASE_5_IMPLEMENTATION.md`, `OPERATIONS.md`, `POSTIZ_DEPLOYMENT.md`, `SECURITY.md`, `brands/nimontron/{BRAND_BRIEF,CONTENT_STRATEGY}.md`.
