@echo off
rem Closes the AI Storyteller (same as the Quit button). Your stories are saved continuously.
cd /d "%~dp0"
".venv\Scripts\python.exe" launcher.py stop
pause
