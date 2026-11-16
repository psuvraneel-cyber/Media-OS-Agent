# Media OS self-healer (Windows)

`watchdog.ps1` runs every 5 minutes from Task Scheduler and fixes what needs Docker on the host.

| Problem | What it does |
|---|---|
| Postiz's publishing worker has no live Temporal pollers for two checks (about 10 min) | `docker restart postiz-runtime-app` (its startup gate waits for Temporal) |
| Postiz's orchestrator restarted 3+ times in 5 min (crash loop) | `docker restart postiz-runtime-app` |
| A Media OS or Postiz container is running but unhealthy for two checks | `docker restart <container>` |
| Docker Desktop isn't running | Telegram alert (at most hourly) |

**Telegram.** Every action is reported there when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are in `C:\Media OS Agent\media-os-runtime\.env`.

**Cooldown.** Each container gets a 15-minute cooldown, so it is never restarted in a loop. If a container is still broken after the cooldown, Telegram says so once.

**Safety.** It never stops, removes or prunes anything, and never touches volumes. Stopped or missing containers are left alone.

## Install / remove

```powershell
powershell -ExecutionPolicy Bypass -File install-watchdog.ps1
powershell -ExecutionPolicy Bypass -File install-watchdog.ps1 -Uninstall
```

- The task runs as you, only while you're signed in, and needs no admin rights.
- It points at this folder, so install it from your main checkout (`C:\Media OS Agent\media-intelligence-os\infra\ops\windows`), not from a worktree.

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
