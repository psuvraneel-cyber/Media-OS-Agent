<#
.SYNOPSIS
  Installs (or removes) the Media OS scheduled tasks that run while you are signed in:
  * "Media OS Watchdog"          - watchdog.ps1 every 5 minutes (the self-healer).
  * "Media OS Restart Requests"  - ops-requests.ps1 every minute (the dashboard's Restart Postiz).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-watchdog.ps1
  powershell -ExecutionPolicy Bypass -File install-watchdog.ps1 -Uninstall

.NOTES
  Runs as you, only while you are signed in (Docker Desktop runs in your session too). No admin
  rights needed. The tasks point at this folder, so keep the scripts where they are.
#>
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$Tasks = @(
    @{
        Name = 'Media OS Watchdog'
        Launcher = 'run-watchdog.vbs'
        Minutes = 5
        Limit = 4
        Description = 'Media OS self-healer: restarts a disconnected Postiz worker or unhealthy containers, and reports to Telegram.'
    },
    @{
        Name = 'Media OS Restart Requests'
        Launcher = 'run-ops-requests.vbs'
        Minutes = 1
        Limit = 3
        Description = 'Media OS: carries out Restart Postiz requests made from the dashboard.'
    }
)

if ($Uninstall) {
    foreach ($t in $Tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Output "Removed scheduled task '$($t.Name)'."
        } else {
            Write-Output "Scheduled task '$($t.Name)' is not installed."
        }
    }
    return
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

foreach ($t in $Tasks) {
    $launcher = Join-Path $PSScriptRoot $t.Launcher
    if (-not (Test-Path $launcher)) { throw "Missing $launcher" }
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $launcher)
    # Repeats from now and after every sign-in. Two triggers are needed: a repeating "at log on"
    # trigger only begins repeating after the *next* sign-in, so on its own the task wouldn't run
    # again today; the time-based one covers the current session.
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $t.Minutes)
    $atLogOn = New-ScheduledTaskTrigger -AtLogOn -User $user
    $atLogOn.Repetition = $repeat.Repetition
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $t.Limit)
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger @($repeat, $atLogOn) `
        -Settings $settings -Principal $principal -Description $t.Description -Force | Out-Null
    Start-ScheduledTask -TaskName $t.Name
    Write-Output "Installed '$($t.Name)' (every $($t.Minutes) min while signed in)."
}
Write-Output "Log: $env:LOCALAPPDATA\MediaOS\watchdog.log"
