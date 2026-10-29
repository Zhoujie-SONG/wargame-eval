@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" start.py --open-browser
) else (
  python start.py --open-browser
)
pause
