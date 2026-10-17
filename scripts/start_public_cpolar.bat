@echo off
rem ============================================================
rem  mfg-agent-platform - Public Access via cpolar
rem  China-native tunnel; recommended for public access from
rem  mainland networks. Uses bin\cpolar.exe (already vendored).
rem
rem  One-time setup (needs a FREE cpolar account):
rem    1^) Register at https://www.cpolar.com
rem    2^) Copy the authtoken from the dashboard
rem    3^) Run once:  bin\cpolar.exe authtoken <your-token>
rem
rem  Usage : double-click this file, copy the https "Forwarding" URL.
rem  Stop  : scripts\stop_public.bat
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set PORT=8004

rem ---------- [1/3] locate cpolar ----------
set CPEXE=
if exist "bin\cpolar.exe" set CPEXE=bin\cpolar.exe
if not defined CPEXE (
    for /f "delims=" %%i in ('where cpolar 2^>nul') do set CPEXE=%%i
)
if not defined CPEXE (
    echo [ERROR] cpolar not found ^(neither bin\cpolar.exe nor PATH^).
    echo         Download from https://www.cpolar.com/download
    echo         and unzip cpolar.exe into this project's bin\ folder.
    echo.
    pause
    exit /b 1
)

rem ---------- [2/3] make sure local service is up ----------
call :check_health
if errorlevel 1 (
    echo [2/3] Local service not running - starting FastAPI ...
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
    echo [2/3] Local service already running on port %PORT%
)

rem ---------- [3/3] start cpolar ----------
echo.
echo ============================================================
echo   Copy the https "Forwarding" URL printed below, e.g.
echo     https://xxxxxxxx.cpolar.top
echo   Share  <that-url>/demo-ui/  with external users.
echo   Stop with scripts\stop_public.bat
echo ============================================================
echo.
"%CPEXE%" http %PORT%

echo.
if errorlevel 1 (
    echo [ERROR] cpolar exited with an error.
    echo         If it printed "not found authtoken", do this ONCE:
    echo           cd /d "%CD%"
    echo           bin\cpolar.exe authtoken ^<your-token^>
    echo         Free token: https://www.cpolar.com/dashboard
    echo.
    pause
    exit /b 1
)
echo Tunnel closed.
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
