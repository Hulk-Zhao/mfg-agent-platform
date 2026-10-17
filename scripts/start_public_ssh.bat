@echo off
rem ============================================================
rem  mfg-agent-platform - Public Access via SSH tunnel (pinggy)
rem
rem  Same "local service + public tunnel + auto URL" pattern as
rem  start_public.bat, but the carrier is SSH over TCP 443 instead
rem  of Cloudflare's edge port 7844. Needed on networks where 7844
rem  is blocked (China + TUN-mode VPN is the common case).
rem
rem  No install: uses the system OpenSSH client.
rem  Anonymous tunnels are quota-limited by the provider (they stop
rem  being handed out after a few tries, then the server asks for
rem  auth). For reliable use, get a free pinggy.io token and set:
rem      set PINGGY_TOKEN=your-token
rem  Free anonymous tunnel expires after 60 minutes.
rem
rem  Usage : double-click this file, then share the printed URL.
rem  Stop  : scripts\stop_public.bat
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set PORT=8004
set TLOG=logs\pinggy_tunnel.log
set URLLOG=logs\public_url.txt

set PINGGYHOST=a.pinggy.io
if defined PINGGY_TOKEN set PINGGYHOST=%PINGGY_TOKEN%@a.pinggy.io

if not exist logs mkdir logs

rem ---------- [1/4] locate ssh client ----------
set SSHEXE=%SystemRoot%\System32\OpenSSH\ssh.exe
if not exist "%SSHEXE%" (
    for /f "delims=" %%i in ('where ssh 2^>nul') do set SSHEXE=%%i
)
if not exist "%SSHEXE%" (
    echo [ERROR] ssh client not found.
    echo         Enable it with: Settings ^> Apps ^> Optional features
    echo         ^> View features ^> OpenSSH Client
    echo         Alternative: scripts\start_public_cpolar.bat
    pause
    exit /b 1
)

rem ---------- [2/4] make sure local service is up ----------
call :check_health
if errorlevel 1 (
    echo [2/4] Local service not running - starting FastAPI ...
    where uv >nul 2>nul
    if errorlevel 1 (
        set PYCMD=.venv\Scripts\python.exe
    ) else (
        set PYCMD=uv run python
        echo       initializing demo database ^(best-effort^) ...
        uv run python -m mfg_agents.common.init_db >nul 2>nul
    )
    start "mfg-agent-platform API" /min cmd /c "!PYCMD! -m uvicorn mfg_agents.v2.demo.api:app --host 0.0.0.0 --port %PORT% > logs\api.log 2>&1"
    call :wait_ready
) else (
    echo [2/4] Local service already running on port %PORT%
)

rem ---------- [3/4] start SSH reverse tunnel ----------
echo [3/4] Starting SSH tunnel ^(pinggy, port 443^) ...
del /q "%TLOG%" >nul 2>nul
start "mfg-tunnel-ssh" /min cmd /c "%SSHEXE% -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -n -R0:localhost:%PORT% %PINGGYHOST% > %TLOG% 2>&1"

echo       Waiting for public URL ^(up to 45s^) ...
set PUBURL=
for /l %%i in (1,1,18) do (
    if "!PUBURL!"=="" (
        for /f "delims=" %%u in ('powershell -NoProfile -Command "$m = Select-String -Path '%TLOG%' -Pattern 'https://[a-z0-9.-]+\.pinggy-free\.link' -ErrorAction SilentlyContinue ^| ForEach-Object { $_.Matches[0].Value } ^| Select-Object -Last 1; if ($m) { $m }" 2^>nul') do set PUBURL=%%u
        if "!PUBURL!"=="" "%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul 2>nul
    )
)
if not defined PUBURL (
    echo [ERROR] No public URL. Check %TLOG%
    echo         ^(port 443 to a.pinggy.io unreachable - try a different
    echo          VPN node, or scripts\start_public_cpolar.bat^)
    pause
    exit /b 1
)
echo %PUBURL%>"%URLLOG%"

rem ---------- [4/4] verify the public URL really serves the app ----------
echo [4/4] Verifying public URL ...
set VOK=
for /l %%i in (1,1,12) do (
    if not defined VOK (
        powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri '%PUBURL%/api/v2/health' -UseBasicParsing -TimeoutSec 8; if($r.StatusCode -eq 200){exit 0}}catch{exit 1}"
        if not errorlevel 1 set VOK=1
        if not defined VOK "%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul 2>nul
    )
)
if not defined VOK (
    echo [WARN] Public URL did not answer /api/v2/health yet.
    echo         The tunnel is up; give it another 30s and reload:
    echo         %PUBURL%/demo-ui/
    echo.
    pause
    exit /b 0
)
rem ---------- summary ----------
set LANIP=
rem filter out virtual adapters: 169.254 (APIPA), 192.168.88 (VMware),
rem 172.28 (WSL), 198.18 (TUN-mode VPN fake-ip interface)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /C:"IPv4" ^| findstr /V "169.254" ^| findstr /V "192.168.88" ^| findstr /V "172.28" ^| findstr /V "198.18."') do set LANIP=%%a
set LANIP=%LANIP: =%

echo.
echo ============================================================
echo   PUBLIC ACCESS READY ^(verified^)
echo.
echo   Share this URL with external users:
echo     %PUBURL%/demo-ui/
echo.
echo   Local access:
echo     http://localhost:%PORT%/demo-ui/
if defined LANIP echo     http://%LANIP%:%PORT%/demo-ui/   ^(LAN^)
echo.
echo   Notes:
echo     - Anonymous SSH tunnel expires after 60 minutes
echo     - URL changes on each restart
echo     - Demo login is passwordless: anyone with the URL can enter
echo     - Stop everything: scripts\stop_public.bat
echo     - URL also saved to: %URLLOG%
echo ============================================================
echo.
pause
exit /b 0

rem ---------- subroutines ----------
:check_health
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/v2/health' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){exit 0}}catch{exit 1}"
exit /b %errorlevel%

:wait_ready
set /a n=0
:wait_loop
call :check_health
if not errorlevel 1 exit /b 0
set /a n+=1
if !n! geq 30 (
    echo [WARN] Service did not become ready within 60s - continuing anyway.
    exit /b 1
)
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul 2>nul
goto :wait_loop
