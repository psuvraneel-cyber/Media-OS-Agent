# Media OS self-healer (Windows)

`watchdog.ps1` runs every 5 minutes from Task Scheduler and fixes what needs Docker on the host.

| Problem | What it does |
|---|---|
| Postiz's publishing worker has no live Temporal pollers for two checks (about 10 min) | `docker restart postiz-runtime-app` (its startup gate waits for Temporal) |
| Postiz's orchestrator restarted 3+ times in 5 min (crash loop) | `docker restart postiz-runtime-app` |
| A Media OS or Postiz container is running but unhealthy for two checks | `docker restart <container>` |
| A Media OS or Postiz container has stopped (exited) | `docker start <container>` |
| The dashboard (`127.0.0.1:3100`) or API (`127.0.0.1:8000`) is healthy in Docker but unreachable from Windows for two checks (Docker lost the port link, usually after its Linux layer restarted) | `docker restart media-os-web` / `media-os-api` |
| Media OS's in-app watchdog hasn't succeeded for 15 min (its alert system is quiet) | Telegram alert (at most hourly) + `docker restart media-os-worker media-os-scheduler` |
| Docker Desktop isn't running | Telegram alert (at most hourly) |

**Telegram.** Every action is reported there when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are in `C:\Media OS Agent\media-os-runtime\.env`.

**Cooldown.** Each container gets a 15-minute cooldown, so it is never restarted in a loop. If a container is still broken after the cooldown, Telegram says so once.

**Safety.** It never stops, removes or prunes anything, and never touches volumes. Stopped containers are started again; missing ones are left alone. To stop the stack on purpose, use `docker compose down` or pause the task first.

**Dead man's switch (PC off alarm).** The self-healer can't tell you the PC is off, so an outside service does:

1. Create a free account at healthchecks.io and add a check: period 5 min, grace 10 min.
2. Under Integrations, add Telegram and follow its steps.
3. Copy the check's ping URL (`https://hc-ping.com/...`) into `C:\Media OS Agent\media-os-runtime\.env` as `HEALTHCHECKS_PING_URL=...`.

Every run pings it. If the pings stop for 15 min, Healthchecks.io messages you, and again when they resume.

## Install / remove

```powershell
powershell -ExecutionPolicy Bypass -File install-watchdog.ps1
powershell -ExecutionPolicy Bypass -File install-watchdog.ps1 -Uninstall
```

- This installs two tasks: **Media OS Watchdog** (the self-healer, every 5 min) and **Media OS Restart Requests** (every minute, see below). Re-running it updates both.
- The tasks run as you, only while you're signed in, and need no admin rights.
- They point at this folder, so install from your main checkout (`C:\Media OS Agent\media-intelligence-os\infra\ops\windows`), not from a worktree.

## Restart Postiz from the dashboard

The small ↻ icon on the dashboard's **Postiz gateway** tile restarts Postiz. The dashboard never touches Docker; it only records a request (`ops_requests` table). `ops-requests.ps1`, run every minute by the **Media OS Restart Requests** task, picks it up and runs `docker restart postiz-runtime-app`. It records the outcome, which the dashboard shows, and messages you on Telegram.

- Only that one action exists, and a click while a request is waiting reuses it.
- A request not picked up within 15 minutes (PC asleep, Docker down) is marked expired rather than run late.
- A second restart within 2 minutes of the last is refused, to give Postiz time to start.
- The pause file below pauses this too.

## Pause, check, test

| To | Do |
|---|---|
| Pause | Create `%LOCALAPPDATA%\MediaOS\watchdog.pause` |
| Resume | Delete that file |
| Read the log | `%LOCALAPPDATA%\MediaOS\watchdog.log` (one line per action; empty when all is well) |
| See the counters | `%LOCALAPPDATA%\MediaOS\watchdog-state.json` |
| Test without restarting anything | `powershell -ExecutionPolicy Bypass -File watchdog.ps1 -DryRun -Verbose` |

The in-app watchdog (`media_os.notify.watchdog`, inside Media OS) handles the alerting side:
- stuck posts
- Postiz not answering
- disabled channels
- the status sync stopping

Both run every 5 minutes.
