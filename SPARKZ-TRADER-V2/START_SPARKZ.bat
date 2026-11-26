@echo off
rem SPARKZ TRADER V2 - one-click start: the server (window 1), the MT5 bridge (window 2) and the dashboard.
rem Settings come from .env (made by SETUP_SPARKZ.bat on the first run). Keep MT5 open and logged in.
setlocal
cd /d "%~dp0"
title SPARKZ launcher

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 and tick "Add python.exe to PATH".
  goto :fail
)

if not exist ".env" (
  echo First start: a few questions to save your settings.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_sparkz.ps1"
  if errorlevel 1 goto :fail
)

if not exist "frontend\dist\index.html" (
  echo Building the dashboard once, this takes a minute ...
  pushd frontend
  call npm install
  call npm run build
  popd
)

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8010/api/bridge/status -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  echo The SPARKZ server is already running.
) else (
  start "SPARKZ 1 - server" /D "%~dp0backend" cmd /k python -m uvicorn app.main:app --port 8010
  echo Starting the server ...
  powershell -NoProfile -Command "for ($i = 0; $i -lt 90; $i++) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8010/api/bridge/status -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep 1 } }; exit 1"
  if errorlevel 1 (
    echo The server did not start: read the error in the "SPARKZ 1 - server" window.
    goto :fail
  )
)

powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*mt5_bridge.py*' }) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  echo The MT5 bridge is already running.
) else (
  start "SPARKZ 2 - MT5 bridge" /D "%~dp0" cmd /k python bridge\mt5_bridge.py
)

start "" http://127.0.0.1:8010
echo.
echo SPARKZ is running. Keep MT5 and the two SPARKZ windows open.
echo STOP_SPARKZ.bat closes them. SETUP_SPARKZ.bat changes the settings.
timeout /t 10 >nul
exit /b 0

:fail
echo.
pause
exit /b 1
