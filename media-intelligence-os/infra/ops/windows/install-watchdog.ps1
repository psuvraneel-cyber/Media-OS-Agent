<#
.SYNOPSIS
  Installs (or removes) the "Media OS Watchdog" scheduled task that runs watchdog.ps1 every
  5 minutes while you are signed in.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-watchdog.ps1
  powershell -ExecutionPolicy Bypass -File install-watchdog.ps1 -Uninstall

.NOTES
  Runs as you, only while you are signed in (Docker Desktop runs in your session too). No admin
  rights needed. The task points at this folder, so keep the scripts where they are.
#>
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'Media OS Watchdog'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Removed scheduled task '$TaskName'."
    } else {
        Write-Output "Scheduled task '$TaskName' is not installed."
    }
    return
}

$launcher = Join-Path $PSScriptRoot 'run-watchdog.vbs'
if (-not (Test-Path $launcher)) { throw "Missing $launcher" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $launcher)
# Every 5 minutes, starting now and after every sign-in. Two triggers are needed: a repeating
# "at log on" trigger only begins repeating after the *next* sign-in, so on its own the task
# wouldn't run again today; the time-based one covers the current session.
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$atLogOn = New-ScheduledTaskTrigger -AtLogOn -User $user
$atLogOn.Repetition = $repeat.Repetition
$trigger = @($repeat, $atLogOn)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Media OS self-healer: restarts a disconnected Postiz worker or unhealthy containers, and reports to Telegram.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed '$TaskName' (every 5 min while signed in). Log: $env:LOCALAPPDATA\MediaOS\watchdog.log"
