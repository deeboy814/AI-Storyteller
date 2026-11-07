@echo off
rem Starts the AI Storyteller so a phone or tablet on your home network can use it too. Other devices must enter a PIN.
rem Only use this on a network you trust.
cd /d "%~dp0"
".venv\Scripts\python.exe" launcher.py lan
