@echo off
REM Summarise the trade data downloaded so far: volume, date span, and which
REM markets have enough arrivals to fit a Hawkes process individually.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\backfill_trades.py --report --out .\data

echo.
pause
