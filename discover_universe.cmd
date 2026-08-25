@echo off
REM Choose a FRESH universe and preview it, without collecting.
REM
REM --min-days-to-close 45 is the important flag. Selecting on volume alone
REM fills the universe with same-day sports: the universe picked on 2026-08-24
REM was already 25%% settled contracts and only a third of it would have
REM survived a six-week window. A basket needs every leg live at once, so a
REM family's usable life is its SOONEST-closing leg.
REM
REM Read the drop tally before trusting the result. If it selects almost
REM nothing, relax --min-volume before relaxing --min-days-to-close.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\collect_kalshi.py --discover --min-days-to-close 45 --min-volume 100 --max-markets 150 --interval 60 %*

echo.
pause
