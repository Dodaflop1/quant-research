@echo off
REM Validate the Hawkes estimator against simulated data with known parameters.
REM
REM Three studies: parameter recovery with confidence-interval coverage, a
REM Poisson negative control, and the seasonality-contamination study. Takes a
REM few minutes. Writes results\diffusion\hawkes_validation.json.

cd /d "%~dp0"

.venv\Scripts\python.exe scripts\hawkes_recovery.py --reps 40 %*

echo.
pause
