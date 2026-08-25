@echo off
REM Backfill Kalshi trade prints for the PINNED UNIVERSE.
REM
REM --tickers-file is not optional in spirit. Measured on 2026-08-24, the whole
REM exchange tape runs about 3.56 million trades per nine hours - roughly
REM 2.8 GB/day, so 90 days is ~250 GB and days of downloading. Restricted to the
REM 150 collected markets it is a few hundred MB and finishes in minutes, and it
REM aligns the trades with the order books already being collected for exactly
REM those markets.
REM
REM --rate 2 because the collector is already using the account's rate limit.
REM
REM Safe to interrupt. Completed windows are checkpointed; re-run to resume.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\backfill_trades.py --days 90 --tickers-file .\data\universe.txt --rate 2 --out .\data %*

echo.
echo ==========================================================
echo  BACKFILL EXITED. Run tally_trades.cmd to see what landed.
echo  Re-run this file to resume if it stopped early.
echo ==========================================================
pause
