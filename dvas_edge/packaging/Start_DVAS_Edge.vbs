' DVAS Edge Operator — Silent Launcher
' Runs Start_DVAS_Edge.bat with a minimised console window (WindowStyle 2).
' This keeps the console attached (Uvicorn needs stdin open) but out of the way.

Set WshShell = CreateObject("WScript.Shell")
WshShell.Run Chr(34) & Replace(WScript.ScriptFullName, WScript.ScriptName, "") & "Start_DVAS_Edge.bat" & Chr(34), 2, False
Set WshShell = Nothing
