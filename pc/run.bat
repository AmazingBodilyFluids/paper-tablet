@echo off
rem Double-click to start. Edit the options below (see README.md).
cd /d "%~dp0"
python rmtablet.py run --monitor 0 %*
pause
