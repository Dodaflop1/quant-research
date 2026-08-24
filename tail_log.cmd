@echo off
REM Follow the collector log live. Safe to open and close at any time;
REM this only reads, and does not touch the running collector.

cd /d "%~dp0"
powershell -NoProfile -Command "Get-Content '.\data\collector.log' -Tail 30 -Wait"
