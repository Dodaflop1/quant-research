@echo off
REM Audit what was actually collected: outages, real sampling interval, and
REM whether the panel is balanced. Run this before writing anything that
REM quotes a sampling rate - the log's "0 errors" does not mean no gaps.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\audit_coverage.py --data .\data --per-market

echo.
pause
