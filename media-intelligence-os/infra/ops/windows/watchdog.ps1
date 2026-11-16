<#
.SYNOPSIS
  Media OS self-healer. Runs every 5 minutes from Windows Task Scheduler (see install-watchdog.ps1).

.DESCRIPTION
  Fixes the failures that need Docker on the host, which the in-app watchdog (inside Media OS)
  can only report:

  1. Postiz's publishing worker is not connected to Temporal (no recent workflow pollers), or it
     is crash-looping (pm2 restart count climbing). That is what silently stopped posts on
     16 Nov 2026. Fix: restart the postiz-runtime-app container; its startup gate makes it wait
     for Temporal before starting.
  2. A Media OS or Postiz container is running but reports "unhealthy" two checks in a row.
     Fix: restart that container.
  3. A Media OS or Postiz container has stopped (exited). Fix: start it again. To stop the stack on
     purpose, use `docker compose down` (removes the containers, which this leaves alone) or pause
     the self-healer first.
  4. Media OS's own alert system has gone quiet: its in-app watchdog hasn't succeeded for 15 min
     (worker or scheduler stuck), so nothing inside Media OS can warn you. Fix: tell you directly
     and restart media-os-worker and media-os-scheduler.
  5. Optional dead man's switch: when HEALTHCHECKS_PING_URL (https://hc-ping.com/...) is in the
     runtime .env, every run pings it. If the pings stop (PC off, asleep, offline, Task Scheduler
     broken), Healthchecks.io alerts you on Telegram from outside.

  Every action is logged and sent to Telegram (when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are
  in the runtime .env). Restarts have a 15-minute cooldown per container so a broken container is
  never restarted in a tight loop; if it is still broken after that, Telegram says so.

  Safe by design:
  * only `docker restart` of known container names; never stop, remove, prune or touch volumes;
  * containers that are stopped or missing are left alone (you may have stopped them on purpose);
  * secrets are read from the runtime .env at run time and never written to the log;
  * create the file %LOCALAPPDATA%\MediaOS\watchdog.pause to pause it.
#>
[CmdletBinding()]
param(
    [string]$RuntimeEnv = 'C:\Media OS Agent\media-os-runtime\.env',
    [int]$CooldownMinutes = 15,
    [int]$PollerFreshSeconds = 120,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$StateDir = Join-Path $env:LOCALAPPDATA 'MediaOS'
$LogFile = Join-Path $StateDir 'watchdog.log'
$StateFile = Join-Path $StateDir 'watchdog-state.json'
$PauseFile = Join-Path $StateDir 'watchdog.pause'

$PostizApp = 'postiz-runtime-app'
$Temporal = 'postiz-runtime-temporal'
$AdminTools = 'postiz-runtime-temporal-admin-tools'
$MediaOsAlertCore = @('media-os-worker', 'media-os-scheduler')
$WatchdogStaleSeconds = 900
$Watched = @(
    'media-os-api', 'media-os-web', 'media-os-worker', 'media-os-scheduler', 'media-os-creative',
    'media-os-postgres', 'media-os-redis', 'postiz-runtime-app', 'postiz-runtime-postgres',
    'postiz-runtime-redis', 'postiz-runtime-temporal'
)

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) {
        Move-Item -Force $LogFile "$LogFile.1"
    }
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Verbose $line
}

function Get-State {
    if (Test-Path $StateFile) {
        try { return Get-Content $StateFile -Raw | ConvertFrom-Json } catch { }
    }
    return [pscustomobject]@{}
}

function Get-Prop($Object, [string]$Name, $Default) {
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p) { return $Default }
    return $p.Value
}

function Set-Prop($Object, [string]$Name, $Value) {
    if ($Object.PSObject.Properties[$Name]) { $Object.$Name = $Value }
    else { $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

function Read-EnvValue([string]$Name) {
    if (-not (Test-Path $RuntimeEnv)) { return '' }
    foreach ($line in Get-Content $RuntimeEnv) {
        if ($line -match "^\s*$Name\s*=\s*(.*)\s*$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return ''
}

function Send-Telegram([string]$Html) {
    $token = Read-EnvValue 'TELEGRAM_BOT_TOKEN'
    $chat = Read-EnvValue 'TELEGRAM_CHAT_ID'
    if (-not $token -or -not $chat) { return }
    try {
        $body = @{ chat_id = $chat; text = $Html; parse_mode = 'HTML'; disable_web_page_preview = $true } |
            ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 15 | Out-Null
    } catch {
        # Never log the exception text: it can contain the request URL (and so the token).
        Write-Log 'telegram: send failed'
    }
}

function Invoke-Docker([string[]]$Arguments) {
    # Windows PowerShell 5.1 turns a native command's stderr into errors; with Stop that throws.
    $ErrorActionPreference = 'Continue'
    $output = & docker @Arguments 2>$null
    return [pscustomobject]@{ Ok = ($LASTEXITCODE -eq 0); Output = ($output -join "`n") }
}

function Get-ContainerState([string]$Name) {
    $r = Invoke-Docker @('inspect', '-f', '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}', $Name)
    if (-not $r.Ok) { return $null }
    $parts = $r.Output.Trim().Split('|')
    return [pscustomobject]@{ Status = $parts[0]; Health = $(if ($parts.Count -gt 1) { $parts[1] } else { '' }) }
}

function Send-Ping([string]$Suffix = '') {
    $url = Read-EnvValue 'HEALTHCHECKS_PING_URL'
    if (-not $url) { return }
    if ($url -notmatch '^https://hc-ping\.com/[A-Za-z0-9_\-/]+$') {
        Write-Log 'healthchecks: HEALTHCHECKS_PING_URL is not an https://hc-ping.com/ URL; skipped'
        return
    }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri ($url + $Suffix) -TimeoutSec 10 | Out-Null
    } catch {
        Write-Log 'healthchecks: ping failed'
    }
}

function Start-Container([string]$Name, [string]$Reason, $State) {
    $key = "lastStart_$Name"
    $last = Get-Prop $State $key $null
    if ($last) {
        $since = (Get-Date) - [datetime]::Parse($last, [Globalization.CultureInfo]::InvariantCulture)
        if ($since.TotalMinutes -lt $CooldownMinutes) { return }
    }
    Write-Log "start ${Name}: $Reason"
    if ($DryRun) { return }
    $r = Invoke-Docker @('start', $Name)
    Set-Prop $State $key ((Get-Date).ToString('o'))
    if ($r.Ok) {
        Send-Telegram ("🛠️ <b>Self-healer started {0}</b>`n{1}" -f $Name, $Reason)
    } else {
        Write-Log "start ${Name} FAILED"
        Send-Telegram ("🔴 <b>Self-healer couldn't start {0}</b>`n{1}`nIt needs a look." -f $Name, $Reason)
    }
}

function Restart-Container([string]$Name, [string]$Reason, $State) {
    $key = "lastRestart_$Name"
    $last = Get-Prop $State $key $null
    if ($last) {
        $since = (Get-Date) - [datetime]::Parse($last, [Globalization.CultureInfo]::InvariantCulture)
        if ($since.TotalMinutes -lt $CooldownMinutes) {
            $warnKey = "cooldownWarned_$Name"
            if (-not (Get-Prop $State $warnKey $false)) {
                Write-Log "skip restart of ${Name} (cooldown): $Reason"
                Send-Telegram ("🔴 <b>Self-healer: {0} is still broken</b>`n{1}`nIt was restarted {2:N0} min ago and didn't recover. It needs a look." -f $Name, $Reason, $since.TotalMinutes)
                Set-Prop $State $warnKey $true
            }
            return
        }
    }
    Write-Log "restart ${Name}: $Reason"
    if ($DryRun) { return }
    $r = Invoke-Docker @('restart', $Name)
    Set-Prop $State $key ((Get-Date).ToString('o'))
    Set-Prop $State "cooldownWarned_$Name" $false
    if ($r.Ok) {
        Send-Telegram ("🛠️ <b>Self-healer restarted {0}</b>`n{1}" -f $Name, $Reason)
    } else {
        Write-Log "restart ${Name} FAILED"
        Send-Telegram ("🔴 <b>Self-healer couldn't restart {0}</b>`n{1}" -f $Name, $Reason)
    }
}

# --------------------------------------------------------------------------------- main
if (Test-Path $PauseFile) { exit 0 }
$state = Get-State

$info = Invoke-Docker @('info', '--format', '{{.ServerVersion}}')
if (-not $info.Ok) {
    # Docker Desktop isn't running: nothing can post. Tell the owner at most once an hour.
    $lastWarn = Get-Prop $state 'dockerDownWarned' $null
    $warnNow = $true
    if ($lastWarn) {
        $warnNow = ((Get-Date) - [datetime]::Parse($lastWarn, [Globalization.CultureInfo]::InvariantCulture)).TotalMinutes -ge 60
    }
    if ($warnNow) {
        Write-Log 'docker is not running'
        Send-Telegram "🔴 <b>Docker isn't running</b>`nMedia OS and Postiz are down, so nothing can post. Start Docker Desktop."
        Set-Prop $state 'dockerDownWarned' ((Get-Date).ToString('o'))
    }
    $state | ConvertTo-Json | Set-Content $StateFile -Encoding utf8
    Send-Ping
    exit 0
}
Set-Prop $state 'dockerDownWarned' $null

# 1. Postiz publishing worker: connected to Temporal, and not crash-looping?
$postiz = Get-ContainerState $PostizApp
$temporal = Get-ContainerState $Temporal
if ($postiz -and $postiz.Status -eq 'running' -and $temporal -and $temporal.Health -eq 'healthy') {
    $uptime = Invoke-Docker @('inspect', '-f', '{{.State.StartedAt}}', $PostizApp)
    $startedMinutesAgo = 999
    if ($uptime.Ok) {
        $startedMinutesAgo = ((Get-Date).ToUniversalTime() - [datetime]::Parse($uptime.Output.Trim()).ToUniversalTime()).TotalMinutes
    }
    # Give a freshly (re)started Postiz 4 minutes to boot: startup gate + Nest + pm2.
    if ($startedMinutesAgo -ge 4) {
        $fresh = 0
        $q = Invoke-Docker @('exec', $AdminTools, 'temporal', 'task-queue', 'describe',
            '--address', 'postiz-runtime-temporal:7233', '--task-queue', 'main', '-o', 'json')
        if ($q.Ok) {
            try {
                $now = (Get-Date).ToUniversalTime()
                foreach ($p in (ConvertFrom-Json $q.Output).pollers) {
                    if ($p.taskQueueType -eq 'workflow' -and
                        ($now - [datetime]::Parse($p.lastAccessTime).ToUniversalTime()).TotalSeconds -le $PollerFreshSeconds) {
                        $fresh++
                    }
                }
            } catch { $fresh = -1 }
        } else { $fresh = -1 }

        # pm2 restart count of the orchestrator. Filtered inside the container so nothing else
        # pm2 prints (it can include environment values) leaves it. No inner double quotes:
        # Windows PowerShell 5.1 strips them when passing arguments to native commands.
        $pm2 = Invoke-Docker @('exec', $PostizApp, 'sh', '-c',
            'pm2 describe orchestrator 2>/dev/null | grep -m1 -E restarts')
        $restarts = -1
        if ($pm2.Ok -and $pm2.Output -match 'restarts\D+(\d+)') { $restarts = [int]$Matches[1] }
        $prevRestarts = [int](Get-Prop $state 'orchestratorRestarts' -1)
        Set-Prop $state 'orchestratorRestarts' $restarts

        $noPollers = [int](Get-Prop $state 'noPollerChecks' 0)
        if ($fresh -eq 0) { $noPollers++ } else { $noPollers = 0 }
        Set-Prop $state 'noPollerChecks' $noPollers

        if ($noPollers -ge 2) {
            Restart-Container $PostizApp "Postiz's publishing worker hasn't been connected to Temporal for about 10 minutes, so scheduled posts can't go out." $state
            Set-Prop $state 'noPollerChecks' 0
        } elseif ($restarts -ge 0 -and $prevRestarts -ge 0 -and ($restarts - $prevRestarts) -ge 3) {
            Restart-Container $PostizApp ("Postiz's publishing worker is crash-looping ({0} restarts in 5 minutes)." -f ($restarts - $prevRestarts)) $state
        }
    }
}

# 2. Containers that stopped, or are running but unhealthy two checks in a row.
foreach ($name in $Watched) {
    $s = Get-ContainerState $name
    $key = "unhealthy_$name"
    if ($s -and $s.Status -in @('exited', 'dead')) {
        Start-Container $name "$name had stopped." $state
        Set-Prop $state $key 0
    } elseif ($s -and $s.Status -eq 'running' -and $s.Health -eq 'unhealthy') {
        $count = [int](Get-Prop $state $key 0) + 1
        Set-Prop $state $key $count
        if ($count -ge 2) {
            Restart-Container $name "$name has reported unhealthy for 10 minutes." $state
            Set-Prop $state $key 0
        }
    } else {
        Set-Prop $state $key 0
    }
}

# 3. Watch the watcher: is Media OS's own alert system still working? Its in-app watchdog runs
#    every 5 min; if it hasn't succeeded for 15 min, nothing inside Media OS can warn you.
$pg = Get-ContainerState 'media-os-postgres'
$scheduler = Get-ContainerState 'media-os-scheduler'
if ($pg -and $pg.Status -eq 'running' -and $scheduler -and $scheduler.Status -eq 'running') {
    $schedStarted = Invoke-Docker @('inspect', '-f', '{{.State.StartedAt}}', 'media-os-scheduler')
    $schedMinutes = 999
    if ($schedStarted.Ok) {
        $schedMinutes = ((Get-Date).ToUniversalTime() - [datetime]::Parse($schedStarted.Output.Trim()).ToUniversalTime()).TotalMinutes
    }
    if ($schedMinutes -ge 15) {
        # Dollar-quoted SQL and no inner double quotes (Windows PowerShell 5.1 strips them).
        $sql = 'psql -U $POSTGRES_USER -d $POSTGRES_DB -At -c ''select coalesce(extract(epoch from now() - max(worker_finished_at))::int, 999999) from job_runs where task_name = $$media_os.notify.watchdog$$ and status = $$SUCCEEDED$$'''
        $age = Invoke-Docker @('exec', 'media-os-postgres', 'sh', '-c', $sql)
        $seconds = -1
        if ($age.Ok -and $age.Output.Trim() -match '^\d+$') { $seconds = [int]$age.Output.Trim() }
        if ($seconds -ge $WatchdogStaleSeconds) {
            $minutes = [math]::Floor($seconds / 60)
            $warnedAt = Get-Prop $state 'alertSystemDownWarned' $null
            $warnNow = $true
            if ($warnedAt) {
                $warnNow = ((Get-Date) - [datetime]::Parse($warnedAt, [Globalization.CultureInfo]::InvariantCulture)).TotalMinutes -ge 60
            }
            if ($warnNow) {
                $shown = $(if ($seconds -ge 999999) { 'a long time' } else { "$minutes min" })
                Write-Log "media os alert system quiet for $shown"
                Send-Telegram ("🔴 <b>Media OS's alert system has gone quiet</b>`nIts watchdog hasn't run for {0}, so Media OS can't warn you about stuck or failed posts. Restarting its worker and scheduler now." -f $shown)
                Set-Prop $state 'alertSystemDownWarned' ((Get-Date).ToString('o'))
            }
            foreach ($core in $MediaOsAlertCore) {
                Restart-Container $core "Media OS's alert system was quiet (watchdog not run for $minutes min)." $state
            }
        } elseif ($seconds -ge 0) {
            Set-Prop $state 'alertSystemDownWarned' $null
        }
    }
}

$state | ConvertTo-Json | Set-Content $StateFile -Encoding utf8

# 4. Dead man's switch: tell Healthchecks.io the PC and the self-healer are alive.
Send-Ping
