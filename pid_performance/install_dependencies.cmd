@echo off
cd /d "%~dp0"
python -m pip install -r host\performance\requirements.txt
if errorlevel 1 pause
