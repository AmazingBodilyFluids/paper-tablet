@echo off
rem Paper Tablet launcher: installs what it needs once, then starts the app without a console.
cd /d "%~dp0pc"
where python >nul 2>&1
if errorlevel 1 (
  echo Paper Tablet needs Python 3.10 or newer.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)
python -c "import webview, paramiko" >nul 2>&1
if errorlevel 1 (
  echo Installing what Paper Tablet needs. This happens once.
  python -m pip install --user -r requirements.txt
  if errorlevel 1 ( pause & exit /b 1 )
)
start "" pythonw app.py
