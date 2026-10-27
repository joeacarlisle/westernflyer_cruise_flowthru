@echo off
REM Launch the flow-through dashboard (needs processed\wf_flowthru.duckdb; run flowthru_build.bat first)
cd /d "%~dp0\..\.."
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m panel serve flowthru_holoviews.py --port 5007 --show
) else (
    python -m panel serve flowthru_holoviews.py --port 5007 --show
)
