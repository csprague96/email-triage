@echo off
rem Opens the dashboard. Starts the watcher too if the background job is not running.
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist %PY% set PY=python
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  start http://127.0.0.1:8765
  exit /b 0
)
echo Watcher is not running in the background. Starting it in this window (close the window to stop).
echo Tip: run install-background.ps1 once so it runs at logon without a window.
%PY% -m emailtriage serve
pause
