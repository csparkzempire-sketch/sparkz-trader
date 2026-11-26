@echo off
rem SPARKZ TRADER V2 - stops the server and the MT5 bridge started by START_SPARKZ.bat.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*mt5_bridge.py*' -or $_.CommandLine -like '*app.main:app --port 8010*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
taskkill /FI "WINDOWTITLE eq SPARKZ 2 - MT5 bridge*" /T /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq SPARKZ 1 - server*" /T /F >nul 2>nul
echo SPARKZ stopped.
echo An open SPARKZ trade stays in MT5 with its stop loss; the robot takes it back over at the next start.
timeout /t 8 >nul
