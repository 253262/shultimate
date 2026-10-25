@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
where python >nul 2>&1
if not errorlevel 1 (
    if not exist .venv\Scripts\python.exe python -m venv .venv
    if errorlevel 1 exit /b 1
    .venv\Scripts\python.exe scripts\setup.py %*
    exit /b %errorlevel%
)
where py >nul 2>&1
if not errorlevel 1 (
    if not exist .venv\Scripts\python.exe py -3 -m venv .venv
    if errorlevel 1 exit /b 1
    .venv\Scripts\python.exe scripts\setup.py %*
    exit /b %errorlevel%
)
echo Please install Python 3.10 or newer and run setup.cmd again.
exit /b 1
