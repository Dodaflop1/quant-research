@echo off
REM Start the Kalshi collector with a PINNED universe.
REM
REM --tickers-file is the important part. Without it, discovery re-selects by
REM live volume on every start, so each restart silently swaps out part of the
REM panel. A coverage audit of the first 39 hours found 298 distinct tickers
REM across three restarts and NOT ONE that spanned the whole window. Pinning
REM keeps every series continuous through a restart.
REM
REM data\universe.txt holds the 150 tickers running since 2026-08-23 19:46 UTC.
REM To choose a fresh universe instead, run discover_universe.cmd first.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\collect_kalshi.py --auto --tickers-file .\data\universe.txt --interval 60 --out .\data %*

echo.
echo ==========================================================
echo  COLLECTOR EXITED. The reason should be above, and is also
echo  in data\collector.log
echo ==========================================================
pause
