@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0host\vendor;%PYTHONPATH%"
if exist D:\mamba\python.exe (
  D:\mamba\python.exe host\performance\app.py
) else (
  python host\performance\app.py
)
if errorlevel 1 pause
