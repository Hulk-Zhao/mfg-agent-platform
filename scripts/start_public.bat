@echo off
rem ============================================================
rem  mfg-agent-platform - Public Access via Cloudflare Tunnel
rem
rem  Exposes the local FastAPI demo (port 8004) to the internet.
rem  Free, no account / no token required.
rem  The public URL is random and changes on every restart.
rem
rem  Usage : double-click this file, then share the printed URL.
rem  Stop  : scripts\stop_public.bat
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set PORT=8004
set URLLOG=logs\public_url.txt
set CFLOG=logs\cloudflared.log

if not exist logs mkdir logs

rem ---------- [1/4] locate cloudflared ----------
set CFEXE=
if exist "bin\cloudflared.exe" set CFEXE=bin\cloudflared.exe
if not defined CFEXE if exist ".tools\cloudflared.exe" set CFEXE=.tools\cloudflared.exe
if not defined CFEXE (
    for /f "delims=" %%i in ('where cloudflared 2^>nul') do set CFEXE=%%i
)
if not defined CFEXE (
    echo [ERROR] cloudflared not found.
    echo.
    echo   Fix ^(either one^):
    echo     a^) Download cloudflared-windows-amd64.exe from
    echo        https://github.com/cloudflare/cloudflared/releases/latest
    echo        and save it as  bin\cloudflared.exe  or  .tools\cloudflared.exe
    echo     b^) Install cloudflared and make sure it is in PATH
    echo.
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

rem ---------- [3/4] start cloudflare quick tunnel ----------
rem --protocol http2: force TCP egress. Default QUIC (UDP) gets intercepted
rem by TUN-mode proxies (Clash fake-ip 198.18.x.x) and fails to dial edge.
echo [3/4] Starting Cloudflare tunnel ...
del /q "%CFLOG%" >nul 2>nul
del /q "%URLLOG%" >nul 2>nul
start "mfg-tunnel" /min "%CFEXE%" tunnel --no-autoupdate --protocol http2 --url http://localhost:%PORT% --logfile "%CD%\%CFLOG%"

echo       Waiting for public URL and edge registration ^(up to 60s^) ...
set PUBURL=
set REGOK=
for /l %%i in (1,1,24) do (
    if "!PUBURL!"=="" call :grab_url
    if "!REGOK!"=="" call :check_reg
    if "!PUBURL!"=="" if "!REGOK!"=="" "%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul 2>nul
)
if not defined PUBURL (
    echo [ERROR] No public URL created. Check %CFLOG%
    echo         ^(no network to Cloudflare - China alternatives:
    echo          scripts\start_public_cpolar.bat or scripts\start_public_ssh.bat^)
    pause
    exit /b 1
)
if not defined REGOK (
    echo.
    echo [ERROR] Tunnel URL was created but the edge connection FAILED.
    echo         The link below will NOT work until the tunnel registers:
    echo           %PUBURL%
    echo.
    echo         Last error from cloudflared:
    powershell -NoProfile -Command "Get-Content '%CFLOG%' -Tail 60 -ErrorAction SilentlyContinue ^| Select-String -Pattern 'ERR|error' ^| Select-Object -Last 2 ^| ForEach-Object { '    ' + $_.Line }"
    echo.
    echo         Usual cause on this kind of network: a TUN-mode VPN ^(fake-ip^)
    echo         hijacks DNS, so cloudflared cannot reach Cloudflare edge
    echo         port 7844 ^(UDP/TCP^). Fixes, easiest first:
    echo           a^) In your VPN client, turn OFF TUN mode ^(keep system proxy^),
    echo              or add "^+.argotunnel.com" to the fake-ip filter list
    echo           b^) Switch to another VPN node, then retry
    echo           c^) Use scripts\start_public_ssh.bat ^(SSH over 443, verified working here^)
    echo           d^) Use scripts\start_public_cpolar.bat ^(cpolar, China-native^)
    echo.
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

rem ---------- [4/4] summary ----------
set LANIP=
rem filter out virtual adapters: 169.254 (APIPA), 192.168.88 (VMware),
rem 172.28 (WSL), 198.18 (TUN-mode VPN fake-ip interface)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /C:"IPv4" ^| findstr /V "169.254" ^| findstr /V "192.168.88" ^| findstr /V "172.28" ^| findstr /V "198.18."') do set LANIP=%%a
set LANIP=%LANIP: =%

echo.
echo ============================================================
echo   PUBLIC ACCESS READY
echo.
echo   Share this URL with external users:
echo     %PUBURL%/demo-ui/
echo.
echo   Local access:
echo     http://localhost:%PORT%/demo-ui/
if defined LANIP echo     http://%LANIP%:%PORT%/demo-ui/   ^(LAN^)
echo.
echo   Notes:
echo     - URL is temporary and changes on each restart
if not defined VOK echo     - [WARN] public URL did not answer health check yet
echo     - Demo login is passwordless: anyone with the URL can enter
echo     - Stop everything: scripts\stop_public.bat
echo     - URL also saved to: %URLLOG%
echo ============================================================
echo.
pause
exit /b 0

rem ---------- subroutines ----------
rem cloudflared writes the quick tunnel URL inside a JSON line:
rem   "|  https://xxx-yyy.trycloudflare.com            |"
rem Grab it with findstr + token split. Do NOT use PowerShell here:
rem its $var does not survive `for /f` inside a cmd subroutine.
rem (Verified 2026-10-17: Select-String version returned empty.)

:grab_url
if not exist "%CFLOG%" exit /b 0
set "RAWHTTP="
for /f "tokens=2" %%u in ('findstr /C:"trycloudflare.com" "%CFLOG%" ^| findstr /C:"https://"') do set "RAWHTTP=%%u"
if not defined RAWHTTP exit /b 0
for /f "tokens=1 delims=,;|} " %%p in ('echo !RAWHTTP!') do set "PUBURL=%%~p"
exit /b 0

:check_reg
if not exist "%CFLOG%" exit /b 0
findstr /C:"Registered tunnel connection" "%CFLOG%" >nul 2>nul && set REGOK=1
exit /b 0

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
