@echo off
REM ============================================================
REM  SeaBreeze Job Management - double-click launcher
REM ============================================================
title SeaBreeze Job Management
cd /d "%~dp0"

echo.
echo   SeaBreeze Roofing - Job Management System
echo   Starting local server (uses http://127.0.0.1:5000, or the next
echo   free port if 5000 is busy). Your browser opens automatically.
echo   (Leave this window open. Close it to stop the app.)
echo.

REM Seed example jobs on first run (skips if jobs already exist)
python seed.py

REM app.py opens the browser automatically a moment after the server starts
python app.py

pause
