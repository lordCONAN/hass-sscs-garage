@echo off
rem Double-click to set up the ESPHome build environment (.venv) for this repo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-env.ps1"
pause
