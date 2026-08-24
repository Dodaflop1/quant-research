@echo off
REM Start the Kalshi collector.
REM
REM Double-click this, or run it from any directory. %~dp0 is the folder this
REM file lives in, so nothing depends on where the shell happens to be - which
REM is what kept breaking when commands used relative paths.
REM
REM The trailing pause keeps the window open if the collector exits, so a crash
REM message stays on screen instead of vanishing with the console.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\collect_kalshi.py --auto --min-volume 100 --max-markets 150 --interval 60 --out .\data %*

echo.
echo ==========================================================
echo  COLLECTOR EXITED. The reason should be above, and is also
echo  in data\collector.log
echo ==========================================================
pause
