@echo off
rem ============================================================
rem  mfg-agent-platform - Stop Public Access
rem
rem  Stops:
rem    1. cloudflared tunnel process
rem    2. SSH tunnel process (a.pinggy.io, matched by command line)
rem    3. FastAPI demo started by start_public*.bat
rem       (matched by command line: uvicorn mfg_agents.v2.demo.api)
rem
rem  Does NOT touch other python / ssh sessions on this machine.
rem ============================================================
cd /d "%~dp0\.."
setlocal

set PORT=8004

echo [1/4] Stopping tunnel ^(cloudflared^) ...
taskkill /IM cloudflared.exe /F >nul 2>nul
echo       done.

echo [2/4] Stopping tunnel ^(ssh a.pinggy.io^) ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='ssh.exe'\" | Where-Object { $_.CommandLine -like '*a.pinggy.io*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo       done.

echo [3/4] Stopping FastAPI demo ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn*mfg_agents.v2.demo.api*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo       done.

echo [4/4] Checking port %PORT% ...
netstat -ano | findstr ":%PORT%" | findstr LISTENING >nul 2>nul
if errorlevel 1 (
    echo       port %PORT% released.
) else (
    echo [WARN] port %PORT% still in use - another process ^(e.g. Docker^) may own it.
)
echo.
echo All stopped.
pause
