@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo LlamaForge debug launcher - console stays visible while the app is running.

set "LF_EXIT=9009"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 run.py
  set "LF_EXIT=%ERRORLEVEL%"
  goto :done
)
where python >nul 2>nul
if not errorlevel 1 (
  python run.py
  set "LF_EXIT=%ERRORLEVEL%"
  goto :done
)

echo Python 3 was not found.
set "LF_EXIT=1"

:done
if not "%LF_EXIT%"=="0" (
  echo.
  echo LlamaForge exited with error code %LF_EXIT%.
  pause
)
exit /b %LF_EXIT%
