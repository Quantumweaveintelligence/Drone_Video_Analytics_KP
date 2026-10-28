@echo off
:: DVAS Edge Operator — Launcher
:: Starts the edge gateway + edge core on localhost:8765
:: The browser will open automatically after ~3 seconds.

echo ============================================================
echo   DVAS Edge Operator — Starting...
echo ============================================================
echo.

"%~dp0python_runtime\python.exe" "%~dp0edge_launcher.py"

echo.
echo DVAS Edge has stopped.
pause
