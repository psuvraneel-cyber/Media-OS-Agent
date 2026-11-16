' Starts ops-requests.ps1 with no visible window. Used by the "Media OS Restart Requests"
' scheduled task (every minute), which carries out "Restart Postiz" clicks from the dashboard.
Dim shell, here
Set shell = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & here & "ops-requests.ps1""", 0, True
