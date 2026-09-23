@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Normal launch: no console window, no pip, UI opens in Edge/Chrome app mode.
where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3 run.py
  exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw run.py
  exit /b 0
)

rem Fallback when the windowed Python launcher is unavailable.
where py >nul 2>nul
if not errorlevel 1 (py -3 run.py & goto :done)
where python >nul 2>nul
if not errorlevel 1 (python run.py & goto :done)

echo Python 3 was not found. Install Python 3, then run this file again.
pause
exit /b 1
:done
if errorlevel 1 pause
