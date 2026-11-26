@echo off
rem SPARKZ TRADER V2 - change the saved settings (.env). Restart SPARKZ afterwards.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_sparkz.ps1"
echo.
pause
