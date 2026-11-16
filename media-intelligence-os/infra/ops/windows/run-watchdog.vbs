' Starts watchdog.ps1 with no visible window (Task Scheduler would otherwise flash a console
' every 5 minutes). Used by the "Media OS Watchdog" scheduled task.
Dim shell, here
Set shell = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & here & "watchdog.ps1""", 0, True
