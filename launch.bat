@echo off
rem Starts the AI Storyteller (if it is not running) and opens it in your browser.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo First-time setup is not done. Run install.bat first. & pause & exit /b 1 )
".venv\Scripts\python.exe" launcher.py start
