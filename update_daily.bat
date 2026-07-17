@echo off
setlocal

cd /d "%~dp0"

echo [update_daily] fetching latest TWSE sector indices...
python fetch_twse_sector.py --update
if errorlevel 1 (
    echo [update_daily] ERROR: fetch_twse_sector.py failed
    exit /b 1
)

echo [update_daily] computing RRG coordinates...
python compute_rrg.py
if errorlevel 1 (
    echo [update_daily] ERROR: compute_rrg.py failed
    exit /b 1
)

echo [update_daily] done.
exit /b 0
