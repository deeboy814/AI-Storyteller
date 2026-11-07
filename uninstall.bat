@echo off
rem Removes the Desktop and Start-menu shortcuts. Your stories and settings (the data folder) are NOT touched.
cd /d "%~dp0"
".venv\Scripts\python.exe" launcher.py unshortcuts
pause
