@echo off
rem ============================================================
rem  mfg-agent-platform - Public Access via Cloudflare Named Tunnel
rem
rem  Stable public URL: https://hulkai.cn/   (no cloud server needed)
rem  No inbound port, no TLS certificate: HTTPS is terminated at the
rem  Cloudflare edge; the tunnel dials out from this machine.
rem
rem  One-time setup:
rem    1) copy deploy\hulkai-cn\tunnel\config.example.yml -> config.yml
rem       and replace ^<UUID^> with the tunnel id from step 3
rem    2) .tools\cloudflared.exe tunnel login
rem    3) .tools\cloudflared.exe tunnel create hulkai
rem    4) .tools\cloudflared.exe tunnel route dns hulkai hulkai.cn
rem       .tools\cloudflared.exe tunnel route dns hulkai www.hulkai.cn
rem    5) in the registrar panel (west.cn) switch DNS to "custom DNS"
rem       and fill in the two nameservers Cloudflare gives you
rem
rem  Usage : double-click this file. Keep the window open.
rem  Stop  : close the window, or scripts\stop_public.bat
rem
rem  Auto-start after reboot (run once in an ADMIN shell, absolute paths):
rem    schtasks /create /tn "mfg-tunnel" /tr "\"D:\tools\mfg-agent-platform\.tools\cloudflared.exe\" tunnel --config \"D:\tools\mfg-agent-platform\deploy\hulkai-cn\tunnel\config.yml\" --no-autoupdate run hulkai" /sc onstart /ru SYSTEM /rl HIGHEST /f
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set PORT=8004
set TUNNEL=hulkai
set CFG=%CD%\deploy\hulkai-cn\tunnel\config.yml

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
    echo         Download cloudflared-windows-amd64.exe from
    echo         https://github.com/cloudflare/cloudflared/releases/latest
    echo         and save it as .tools\cloudflared.exe
    echo.
    pause
    exit /b 1
)

rem ---------- [2/4] check tunnel config ----------
if not exist "%CFG%" (
    echo [ERROR] Tunnel config missing:
    echo         %CFG%
    echo.
    echo         Copy config.example.yml to config.yml in the same folder,
    echo         then replace ^<UUID^> with the id printed by:
    echo           .tools\cloudflared.exe tunnel create %TUNNEL%
    echo.
    pause
    exit /b 1
)
findstr /C:"<UUID>" "%CFG%" >nul 2>nul
if not errorlevel 1 (
    echo [ERROR] config.yml still contains the ^<UUID^> placeholder.
    echo         Run: .tools\cloudflared.exe tunnel create %TUNNEL%
    echo         then put the printed UUID / json path into config.yml.
    echo.
    pause
    exit /b 1
)

rem ---------- [3/4] make sure local service is up ----------
call :check_health
if errorlevel 1 (
    echo [3/4] Local service not running - starting FastAPI ...
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
    echo [3/4] Local service already running on port %PORT%
)

rem ---------- [4/4] run the named tunnel ----------
echo [4/4] Starting named tunnel "%TUNNEL%" ...
echo.
echo   Public URL : https://hulkai.cn/        (302 to /demo-ui/)
echo   Local URL  : http://localhost:%PORT%/demo-ui/
echo.
echo   First request after a restart may take a few seconds while
echo   Cloudflare re-establishes the edge connection.
echo   Stop with Ctrl+C here, or scripts\stop_public.bat
echo ============================================================
"%CFEXE%" tunnel --config "%CFG%" --no-autoupdate run %TUNNEL%

echo.
echo Tunnel process exited. If this was unexpected, see the output above.
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
