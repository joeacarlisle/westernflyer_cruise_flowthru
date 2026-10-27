@echo off
REM Build the flow-through DuckDB for one cruise:  flowthru_build.bat <cruise_id> [options]
cd /d "%~dp0\..\.."
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe main.py %*
) else (
    python main.py %*
)
echo.
echo Build finished (exit code %ERRORLEVEL%).
pause
