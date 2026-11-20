# Handoff: Media OS current state

**Updated:** 20 Nov 2026 (main at `6d112d1`, PR #38; release **v1.0.0** plus PRs #32–#38).

**Owner goal:** market the Nimontron Android app on Instagram and Facebook, and later on YouTube.

**How content flows today:**
1. **Create:** Media OS drafts carousels with free-tier AI (daily, or from the Studio Generate button), or the owner uploads their own reels, photos and carousels in Studio.
2. **Approve:** everything is checked automatically and approved by the owner.
3. **Publish:** Postiz publishes at the scheduled time.
4. **Monitor:** Telegram reports problems and a nightly summary.

**Constraints:**
- zero budget;
- every post approved by a human;
- Reddit is never used.

---

## 1. Where things stand (20 Nov 2026)

| Area | State |
|---|---|
| Research ingestion (Phase 5) | Built; all 5 sources **OFF** and tech-only. Reddit is **BLOCKED** at the database level. Not connected to generation yet. |
| AI router (N1) | Live. Gemini → Groq → OpenRouter (`:free`) → Cloudflare, with failover. Gemini 2.5 Flash writes most posts. |
| Carousels (N2) | Live. Writer + text and render QA + score; built-in design v2, or the owner's **design templates** (none uploaded yet). |
| Autopilot | Live. **Daily drafts at 08:00 IST** (set to **3/day**), the Studio **Generate** button (1–3 at a time, 10/day limit), topic, hook and language rotation, and a **no-repeat check** against recent subjects. **Auto-schedule is off.** |
| Publishing (N3) | Live. Carousels as JPEG and reels as MP4 go to GitHub Pages, then Postiz, then Instagram and Facebook. Status sync every 15 min; one automatic retry for temporary errors. **Posts per day: 4** per platform (set in Studio). |
| Studio | Live. Upload your own reels (MP4 ≤95 MB), photo posts and carousels (2–10 images), caption, approve, batch-schedule. **2 owner reels already published.** |
| Analytics | Live. Per-post metrics (every 6 h, nightly 21:40, **Sync now**) plus **whole-account** Instagram and Facebook totals (daily 21:35). |
| Alerts | Live. Telegram alerts for failed, stuck and overdue posts, a **22:00 nightly report**, and bot commands `/status` `/queue` `/help`. |
| Self-healer | Live (Windows Task Scheduler). It restarts a broken Postiz worker and unhealthy or stopped containers, watches the in-app watchdog, rebuilds lost dashboard and API port links, starts **Docker on demand 1 h before each post**, and carries out the dashboard's **Restart Postiz** requests. |
| Review and publishing fixes (PR #40) | Edit a carousel's text before approving; rejection notes and the owner's edits steer the writer; **cancel or move** a scheduled post from Media OS; daily clean-up of hosted media (`publish.cleanup_media`, migration 0016); cross-language repeat check; live process status on the Agents page. |
| Results so far | Reels ≈1,800 Instagram views each; carousels ≈4; Facebook ≈0. Video (N4) and learning from results (N5) are the priorities. |
| Content counts | 8 carousels + 2 reels published · 2 carousels scheduled (4 platform posts, last 21 Nov 19:00) · **4 drafts waiting in Approvals** · 1 render-failed and 1 text-failed draft · 1 rejected. |

## 2. How to use it

- **Dashboard:** http://127.0.0.1:3100, or the **Media OS desktop app** (Electron, Windows Acrylic). Log in as `local-owner`. Reset the password with `docker exec -it media-os-api python -m media_os_api.auth.rotate_password --username local-owner`.
- **Studio:**
  - **Generate posts:** choose topic, hook, language and design, or leave each on "Let Media OS choose".
  - **Autopilot card:** daily drafts (0–3), **posts per day** per platform (1–10), auto-schedule on/off, and posting times (up to 3).
  - **Upload:** reels, photos and carousels; then caption, approve and schedule.
- **Approvals:**
  - **Review tab:** generated carousels with QA checks, including the `novelty` warning, and the score; **Edit text** (re-checks and re-renders), approve or reject. Rejection notes and edits are given to the writer for the next drafts.
  - **Approved tab:** pick a time and channels, then **Schedule**. Scheduled posts have **Move** and **Cancel post** (all channels; not in the last 5 minutes).
- **Assets:**
  - **Brand logo**, shown beside the brand name and in the switcher.
  - **Design templates:** cover, content and closing backgrounds at 1080×1350, each with a text area (top/middle/bottom) and text colour (light/dark), plus occasion tags.
  - **Library** of reels, covers and slides.
- **Analytics:** per-post numbers, the **Whole account** card (7 or 30 days), and **Refresh analytics**.
- **Dashboard home:** the ↻ icon on the **Postiz gateway** tile restarts Postiz. A Windows task does it within a minute; the web app never gets Docker access.
- **Telegram:** `/status`, `/queue` and `/help` (owner chat only). Alerts and the 22:00 report arrive automatically.
- **CLI, if ever needed:** `docker exec media-os-worker python -m media_os_api.carousel.admin generate --pillar … --hook … --language …`. Use `draft` instead of `generate` to preview without saving.
- **Postiz:** http://localhost:4007 (separate stack). Cancelling in Media OS deletes the post there; deleting it in Postiz directly still works (the sync marks it CANCELLED).

## 3. Runtime

- **Media OS stack:** `infra/docker/compose.app.yml`, with env in `C:\Media OS Agent\media-os-runtime\.env`.
  - **Containers:** `media-os-{api,worker,scheduler,web,creative,postgres,redis}`.
  - **Database:** at migration **`0015_design_templates`**. PR #40 adds **`0016_media_cleanup`** (one schedule row, no table changes): back up, then migrate on deploy.
  - **Deploy:** pull the main checkout `C:\Media OS Agent`, then run `docker compose --env-file "C:/Media OS Agent/media-os-runtime/.env" -f infra/docker/compose.app.yml up -d --build --wait`, then `docker exec media-os-api alembic upgrade head`.
  - **Back up first:** `scripts/backup_db.py` (dumps go to `C:\Media OS Agent\media-os-backups`; latest `media_os_20261117T094130Z.dump`).
- **Volumes:**

  | Container | Role | Secrets | `media_os_media` (rendered) | `media_os_uploads` (owner files) |
  |---|---|---|---|---|
  | api | HTTP API | none | read-only | **read-write** (only writer) |
  | worker | AI, publishing, analytics (`control`, `maintenance`, `default`, `research` queues) | AI, Postiz, GitHub, Telegram | read-only | read-only |
  | creative | headless Chromium on the `creative` queue | none | read/write | read-only (template backgrounds) |
  | web | Next.js dashboard | none | — | — |

- **Schedules:** all enabled except the 5 `research.fetch.*`.
  - `publish.sync_status`: 15 min.
  - `analytics.sync_post_metrics`: 6 h, plus `.nightly` at 21:40.
  - `analytics.sync_account_metrics`: 21:35.
  - `content.daily_drafts`: 08:00.
  - `publish.cleanup_media`: 03:30 (after 0016).
  - `notify.watchdog`: 5 min.
  - `notify.telegram_commands`: 1 min.
  - `notify.daily_digest`: 22:00.
  - `maintenance.purge_job_history`.
- **Env keys** (key names only, never values):
  - **AI:** `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
  - **Publishing:** `POSTIZ_API_KEY`, `NIMONTRON_MEDIA_GITHUB_TOKEN`, `NIMONTRON_MEDIA_GITHUB_REPO`, `NIMONTRON_MEDIA_BASE_URL`.
  - **Alerts:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MEDIA_OS_DASHBOARD_URL`.
  - **Optional:** `HEALTHCHECKS_PING_URL`, **not set yet**.
- **Postiz:** env in `C:\Media OS Agent\postiz-runtime\.env`.
  - Meta app "Nimontron Publisher", in Development mode. That's fine for the owner's own accounts: posts are public and no App Review is needed.
  - Channels: Instagram `@com.nimontron.app` and the Facebook Page "Nimontron: Bengali Invitations".
  - It has a startup gate that waits for Temporal. The Temporal web UI is stopped (`--restart=no`). Elasticsearch still runs.
- **Media hosting:** public repo `psuvraneel-cyber/nimontron-media` on GitHub Pages. After PR #40, `publish.cleanup_media` deletes a post's files 3 days after it publishes (15 if a platform failed). Deleted files still count in the repo's git history, so its size grows more slowly but doesn't shrink.
- **Windows host** (scripts in the main checkout's `infra/ops/windows`):
  - **Tasks:** **Media OS Watchdog** (every 5 min) and **Media OS Restart Requests** (every 1 min). Reinstall with `install-watchdog.ps1`.
  - **Files:** in `%LOCALAPPDATA%\MediaOS`: `watchdog.log`, `watchdog-state.json`, `upcoming-posts.json` (post times saved for Docker on demand), and `watchdog.pause` (create it to pause).
  - **WSL2 limits:** `C:\Users\Sauvraneel Paul\.wslconfig` caps Docker at **4 cores, 5 GB RAM, 2 GB swap** (the laptop was overheating). Delete the file and restart Docker to undo.
  - **Docker Desktop:** `C:\Users\Sauvraneel Paul\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`. The owner quits it to keep the laptop cool; the self-healer starts it 1 h before each post.
- **GitHub:**
  - private repo `psuvraneel-cyber/Media-OS-Agent`;
  - releases v0.5.0–v1.0.0 (v1.0.0 is latest);
  - 19 repo topics;
  - the README was refreshed for v1.0.

## 4. Safety rules in force

- **Approval:** nothing publishes without owner approval plus a schedule (manual, batch, or auto-schedule when the owner turns it on). Owner uploads are never auto-scheduled.
- **Claims:** content may only use claims from `carousel/brand_kit.py`. QA blocks:
  - rankings and install numbers;
  - testimonials;
  - Christian and Buddhist cards (wording not yet approved);
  - iOS mentions;
  - India-only prices shown without saying so;
  - Devanagari text;
  - a premium design named next to a different card.

  Tagore mentions are a warning only. Near-repeats of recent subjects get a `novelty` warning after one rewrite.
- **Assets:** never use `raw/card-2.png`, `screenshots/3.png` or `screenshots/4.png` (the wrong Tagore credit).
- **Budget and credentials:**
  - no paid services;
  - never print, log or commit secrets;
  - never read `.env` values (key-name checks only);
  - the owner signs in and types passwords themselves.
- **Git and runtime:**
  - branch, then PR to **`main`**, then green CI, then merge, then deploy;
  - never use `--no-verify`;
  - back up before any migration;
  - no destructive Docker commands;
  - Postiz stays an independent stack;
  - don't start Docker Desktop for the owner unless asked (they turn it off on purpose).
- **Stacked PRs:** always target `main` directly. #35 was merged into its parent branch and had to be re-opened as #36.

## 5. Open items

1. **Remove Postiz's Elasticsearch** (saves ~700 MB and CPU spikes).
   - **Blocked while any post is scheduled:** Postiz searches it to cancel a post's job (that includes **Cancel post** and **Move** in Media OS), and existing jobs wouldn't be findable afterwards.
   - **When:** after the last scheduled post (currently 21 Nov 19:00), with nothing new scheduled.
   - **Steps:** back up the Postiz setup and Temporal database; set `ENABLE_ES=false` (Temporal uses its SQL visibility store) and stop Elasticsearch, keeping its volume; then test scheduling and cancelling a post.
2. **Disable Docker's Windows startup entry:** Task Manager → Startup apps → Docker Desktop → Disable. It's a leftover HKCU Run entry; Docker's own setting is already off.
3. **Healthchecks.io PC-off alarm:** free account → 5-min check → Telegram integration → put `HEALTHCHECKS_PING_URL` in the runtime `.env`.
4. **4 drafts waiting in Approvals.** The older শ্রী/শ্রীমতী draft repeats a published post; reject it.
5. **YouTube Shorts:**
   - **Owner:** create a Google Cloud project, the YouTube Data API OAuth client (redirect `http://localhost:4007/integrations/social/youtube`, consent screen in Production), and apply for the YouTube API audit. Unaudited uploads stay private forever.
   - **Media OS:** a privacy-policy page and Shorts support, built off until approved.
6. **Before adding a second brand:**
   - **Link accounts to brands.** Scheduling currently posts to every connected Postiz channel.
   - Then add a **New brand** form.
7. **Research for Nimontron:** the current sources are tech-only. Add wedding, festival and occasion sources, then feed research into generation.
8. **Housekeeping:**
   - ~~GitHub Pages repo size~~: published media is now deleted daily (PR #40); history still grows;
   - staged Studio images that were never turned into posts (needs a daily cleanup);
   - the creative image is 3.76 GB;
   - ~~hook text shows `*asterisks*`~~: fixed in PR #40.
9. **Local quirks:**
   - commits from a worktree need `MEDIA_OS_VENV_PATH="C:/Media OS Agent/media-intelligence-os/.venv"` and `MEDIA_OS_WORKSPACE_ROOT="C:/Media OS Agent"`;
   - `test_manifest_verification_on_real_references` fails in worktrees (CI skips it);
   - the Claude desktop sandbox virtualizes writes to `%LOCALAPPDATA%` (test copies land in `Packages\Claude_…\LocalCache`);
   - Windows PowerShell 5.1 strips inner double quotes from native arguments (scripts use `$$` SQL quoting).

## 6. Key docs

- `README.md` (overview)
- `docs/OPERATIONS.md` (alerts, autopilot, Studio, analytics, self-healer)
- `infra/ops/windows/README.md` (host tasks)
- `docs/N2_CAROUSEL_PIPELINE.md`
- `docs/N3_PUBLISHING.md`
- `docs/ROADMAP.md`
- `docs/POSTIZ_DEPLOYMENT.md`
- `docs/SECURITY.md`
- `docs/brands/nimontron/{BRAND_BRIEF,CONTENT_STRATEGY}.md`
