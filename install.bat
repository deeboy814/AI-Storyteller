@echo off
rem AI Storyteller installer for Windows. Safe to run again. Nothing is installed system-wide.
cd /d "%~dp0"
echo.
echo 1/3  Checking Python
set PYCMD=
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PYCMD=py -3
if not defined PYCMD (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PYCMD=python
)
if not defined PYCMD (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run install.bat again.
  pause
  exit /b 1
)
echo.
echo 2/3  Installing the app's libraries (this downloads a few small packages)
%PYCMD% -m venv .venv
if errorlevel 1 ( echo Could not create the .venv folder. & pause & exit /b 1 )
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 ( echo Installing the libraries failed. Check your internet connection and run install.bat again. & pause & exit /b 1 )
if not exist data mkdir data
echo.
echo 3/3  Desktop and Start-menu shortcuts
".venv\Scripts\python.exe" launcher.py shortcuts
echo.
echo All set. Double-click "AI Storyteller" on your Desktop to start.
echo Local AI models are optional: install Ollama from https://ollama.com if you want them.
echo Then open Settings (the gear) and choose your AI. The README explains the first settings to check.
pause
