@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create .venv and install the package with the mcp extra first.
  echo See docs\HUB_RUNBOOK.md for setup instructions.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m research_agent.hub_cli init
if errorlevel 1 (
  pause
  exit /b 1
)
echo API docs: http://127.0.0.1:8765/docs
echo Credentials: data\hub\access.local.json
echo Use Ctrl+C to stop. No sample or internal data is imported automatically.
".venv\Scripts\python.exe" -m research_agent.hub_cli serve
pause
