@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Python environment is missing. Run the setup PowerShell script first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "start.py"
