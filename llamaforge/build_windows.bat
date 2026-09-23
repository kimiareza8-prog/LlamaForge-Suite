@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if not errorlevel 1 (py -3 -m venv .venv) else (python -m venv .venv)
)
set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install pyinstaller
if errorlevel 1 goto :fail
"%PY%" -m PyInstaller --noconfirm --clean --name LlamaForge --add-data "llamaforge\web\static;llamaforge\web\static" run.py
if errorlevel 1 goto :fail
echo.
echo Build complete. Check dist\LlamaForge\
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
