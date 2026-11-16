<#
.SYNOPSIS
  Carries out "Restart Postiz" requests made from the Media OS dashboard. Runs every minute from
  Windows Task Scheduler (task "Media OS Restart Requests", see install-watchdog.ps1).

.DESCRIPTION
  The dashboard never touches Docker; it only records a request in the ops_requests table. This
  script, running as you on the host, picks up the oldest waiting request, restarts the
  postiz-runtime-app container (its startup gate waits for Temporal) and records the outcome.

  Only that one action exists. Requests waiting longer than 15 minutes (PC off, Docker down) are
  marked EXPIRED rather than run late. Uses the self-healer's log, pause file and Telegram.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops-requests.ps1 -DryRun -Verbose
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$RuntimeEnv = 'C:\Media OS Agent\media-os-runtime\.env'
)

$ErrorActionPreference = 'Stop'
$StateDir = Join-Path $env:LOCALAPPDATA 'MediaOS'
$LogFile = Join-Path $StateDir 'watchdog.log'
$PauseFile = Join-Path $StateDir 'watchdog.pause'
$PostizApp = 'postiz-runtime-app'
$Postgres = 'media-os-postgres'

if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Force $StateDir | Out-Null }

function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} restart-requests: {1}' -f (Get-Date), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Verbose $line
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

function Invoke-Sql([string]$Sql) {
    # Dollar-quoted SQL and no inner double quotes (Windows PowerShell 5.1 strips them).
    $cmd = 'psql -U $POSTGRES_USER -d $POSTGRES_DB -At -c ''' + $Sql + ''''
    return Invoke-Docker @('exec', $Postgres, 'sh', '-c', $cmd)
}

# --------------------------------------------------------------------------------- main
if (Test-Path $PauseFile) { exit 0 }
if (-not (Invoke-Docker @('info', '--format', '{{.ServerVersion}}')).Ok) { exit 0 }

# Requests nobody could act on in time are closed, not run late.
[void](Invoke-Sql ('update ops_requests set status = $$EXPIRED$$, finished_at = now(), ' +
    'result_detail = $$Not picked up within 15 minutes (PC or Docker was off).$$ ' +
    'where status = $$PENDING$$ and requested_at < now() - interval $$15 minutes$$'))

if ($DryRun) {
    $peek = Invoke-Sql ('select count(*) from ops_requests where status = $$PENDING$$ and ' +
        'action = $$RESTART_POSTIZ$$')
    Write-Log ("dry run: {0} waiting request(s)" -f $peek.Output.Trim())
    exit 0
}

$claim = Invoke-Sql ('update ops_requests set status = $$RUNNING$$, picked_up_at = now() ' +
    'where id = (select id from ops_requests where status = $$PENDING$$ and ' +
    'action = $$RESTART_POSTIZ$$ order by requested_at limit 1 for update skip locked) ' +
    'returning id')
if (-not $claim.Ok) { Write-Log 'could not read requests'; exit 0 }
$id = ($claim.Output -split "`n" | Where-Object { $_ -match '^[0-9a-f-]{36}$' } | Select-Object -First 1)
if (-not $id) { exit 0 }

Write-Log "restarting $PostizApp (dashboard request $id)"
$r = Invoke-Docker @('restart', $PostizApp)
if ($r.Ok) {
    [void](Invoke-Sql ('update ops_requests set status = $$DONE$$, finished_at = now(), ' +
        'result_detail = $$Postiz restarted. Publishing reconnects in about a minute.$$ ' +
        "where id = `$`$$id`$`$"))
    Send-Telegram ("🔁 <b>Postiz restarted</b>`nYou asked for it from the dashboard. Publishing reconnects in about a minute.")
} else {
    Write-Log "restart of $PostizApp FAILED"
    [void](Invoke-Sql ('update ops_requests set status = $$FAILED$$, finished_at = now(), ' +
        'result_detail = $$Docker could not restart Postiz. Check Docker Desktop.$$ ' +
        "where id = `$`$$id`$`$"))
    Send-Telegram ("🔴 <b>Couldn't restart Postiz</b>`nDocker refused the restart you asked for from the dashboard. Check Docker Desktop.")
}
