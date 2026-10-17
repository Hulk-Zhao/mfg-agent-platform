@echo off
rem ============================================================
rem  mfg-agent-platform - Public Access via cpolar
rem  China-friendly tunnel with a stable-ish random domain.
rem
rem  Prerequisites:
rem    1. Install cpolar from https://www.cpolar.com
rem    2. Configure your token once:  cpolar authtoken <your-token>
rem
rem  Usage : double-click this file, copy the https "Forwarding" URL.
rem  Stop  : scripts\stop_public.bat
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set PORT=8004

where cpolar >nul 2>nul
if errorlevel 1 (
    echo [ERROR] cpolar not found in PATH.
    echo.
    echo   Setup steps:
    echo     1^) Install cpolar from https://www.cpolar.com
    echo     2^) Configure token: cpolar authtoken ^<your-token^>
    echo     3^) Re-run this script
    echo.
    echo   No-install alternative: scripts\start_public.bat ^(Cloudflare^)
    echo.
    pause
    exit /b 1
)

rem ---------- [1/3] make sure local service is up ----------
call :check_health
if errorlevel 1 (
    echo [1/3] Local service not running - starting FastAPI ...
    where uv >nul 2>nul
    if errorlevel 1 (
        set PYCMD=.venv\Scripts\python.exe
    ) else (
        set PYCMD=uv run python
        uv run python -m mfg_agents.db.init_db >nul 2>nul
    )
    start "mfg-agent-platform API" /min cmd /c "!PYCMD! -m uvicorn mfg_agents.v2.demo.api:app --host 0.0.0.0 --port %PORT% > logs\api.log 2>&1"
    call :wait_ready
) else (
    echo [1/3] Local service already running on port %PORT%
)

rem ---------- [2/3] start cpolar ----------
echo [2/3] Starting cpolar tunnel on port %PORT% ...
echo.
echo   Copy the https "Forwarding" URL printed below, e.g.
echo     https://xxxxxxxx.cpolar.top
echo   Share it with anyone to open the demo UI.
echo.
echo ============================================================
cpolar http %PORT%
echo ============================================================
echo.
echo Tunnel finished. To restart: run this script again.
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
