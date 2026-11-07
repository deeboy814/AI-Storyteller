@echo off
rem Runs the AI Storyteller in this window (close the window or press Ctrl+C to stop). Open http://127.0.0.1:8000
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo First-time setup is not done. Run install.bat first. & pause & exit /b 1 )
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
