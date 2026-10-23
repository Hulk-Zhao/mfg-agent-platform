@echo off
rem ============================================================
rem  mfg-agent-platform - ONE-CLICK PUBLIC START (hulkai.cn)
rem
rem  Brings up everything needed to serve the demo on the internet:
rem    [3/6] middleware (MySQL / Milvus / Redis, docker)   - only when
rem          VECTOR_BACKEND=milvus in .env
rem    [4/6] local FastAPI backend
rem    [5/6] Cloudflare named tunnel
rem    [6/6] public URL self-check
rem
rem  Public URL : https://hulkai.cn/     (302 -> /demo-ui/)
rem  Stop all   : scripts\stop_public.bat
rem
rem  SINGLE SOURCE OF TRUTH: the backend port and hostname are read
rem  from deploy\hulkai-cn\tunnel\config.yml, so this script always
rem  follows whatever that file routes to. Change the port there (and
rem  restart the backend) - no edit needed here.
rem
rem  NOTE: the LLM key lives in the DS_KEY *user* environment
rem  variable, NOT in .env (where it is empty). A shell started under
rem  another account (e.g. Task Scheduler with /ru SYSTEM) does not
rem  inherit it and the demo silently loses its LLM. Run this script
rem  as the logged-in user, or put DS_KEY into .env first.
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set CFG=%CD%\deploy\hulkai-cn\tunnel\config.yml
set TLOG=%CD%\logs\tunnel-hulkai.log
set MILVUS_PORT=19531
set PUBLIC_OK=

if not exist logs mkdir logs

echo ============================================================
echo   mfg-agent-platform  -  public start  (hulkai.cn)
echo ============================================================

rem ---------- [1/6] tunnel config: port + hostname ----------
echo.
echo === [1/6] Reading tunnel config ===
if not exist "%CFG%" (
    echo [ERROR] Missing "%CFG%"
    echo         Copy config.example.yml to config.yml in the same folder
    echo         and fill in the tunnel UUID first.
    pause
    exit /b 1
)
findstr /C:"<UUID>" "%CFG%" >nul 2>nul
if not errorlevel 1 (
    echo [ERROR] config.yml still contains the ^<UUID^> placeholder.
    echo         Run: .tools\cloudflared.exe tunnel create hulkai
    pause
    exit /b 1
)

set PORT=
for /f "tokens=4 delims=: " %%p in ('findstr /C:"service: http://127.0.0.1:" "%CFG%" 2^>nul') do (
    if not defined PORT set PORT=%%p
)
if not defined PORT (
    set PORT=8004
    echo [WARN] No "service: http://127.0.0.1:PORT" line - assuming %PORT%.
) else (
    echo       backend port  : %PORT%   ^(from config.yml^)
)

set DOMAIN=
for /f "tokens=3" %%d in ('findstr /C:"- hostname:" "%CFG%" 2^>nul') do (
    if not defined DOMAIN set DOMAIN=%%d
)
if not defined DOMAIN set DOMAIN=hulkai.cn
echo       public domain : %DOMAIN%

rem ---------- [2/6] locate cloudflared ----------
echo.
echo === [2/6] Locating cloudflared ===
set CFEXE=
if exist "bin\cloudflared.exe" set CFEXE=bin\cloudflared.exe
if not defined CFEXE if exist ".tools\cloudflared.exe" set CFEXE=.tools\cloudflared.exe
if not defined CFEXE (
    for /f "delims=" %%i in ('where cloudflared 2^>nul') do set CFEXE=%%i
)
if not defined CFEXE (
    echo [ERROR] cloudflared not found ^(bin\ .tools\ or PATH^).
    echo         Download cloudflared-windows-amd64.exe from
    echo         https://github.com/cloudflare/cloudflared/releases/latest
    echo         and save it as .tools\cloudflared.exe
    pause
    exit /b 1
)
echo       %CFEXE%

rem ---------- [3/6] middleware ----------
echo.
echo === [3/6] Middleware ^(MySQL / Milvus / Redis^) ===
set VECTOR_BACKEND=chroma
for /f "usebackq tokens=1,2 delims==" %%a in (`findstr /B /C:"VECTOR_BACKEND=" .env 2^>nul`) do set VECTOR_BACKEND=%%b
for /f "usebackq tokens=1,2 delims==" %%a in (`findstr /B /C:"MILVUS_URI=" .env 2^>nul`) do (
    for /f "tokens=3 delims=:" %%p in ("%%b") do set MILVUS_PORT=%%p
)

if /i not "%VECTOR_BACKEND%"=="milvus" (
    echo       vector backend = %VECTOR_BACKEND% - middleware not required.
    goto :backend
)
where docker >nul 2>nul
if errorlevel 1 (
    echo [WARN] docker not found - cannot start middleware.
    echo        The demo degrades: no vector search, no cache, no limiter.
    goto :backend
)
docker compose -f docker-compose.infra.yml up -d
if errorlevel 1 (
    echo [WARN] middleware startup failed - continuing anyway.
) else (
    call :wait_port %MILVUS_PORT% 24
)

:backend
rem ---------- [4/6] backend ----------
echo.
echo === [4/6] FastAPI backend on port %PORT% ===
call :check_health
if not errorlevel 1 (
    echo       already running - reusing it.
    goto :tunnel
)

echo       not running - starting it ...

rem Best effort only: the demo DBs are seeded by scripts\seed_all.bat.
rem init_db is idempotent (INSERT OR IGNORE) so re-running is harmless.
uv run python -m mfg_agents.common.init_db >nul 2>nul

rem Deliberately NO --reload here: the reloader parent spawns a worker
rem child, and a hard kill of the parent leaves that child holding the
rem listening socket (observed on this machine - 7 orphans on 8004).
rem A public-facing instance wants one stable process anyway.
rem
rem Launched via PowerShell Start-Process rather than `start cmd /c`:
rem the cmd-inside-start form silently failed to launch anything when
rem this script itself was invoked from a non-interactive shell.
set BACKEND_EXE=%CD%\.venv\Scripts\python.exe
set BACKEND_ARGS='-m','uvicorn','mfg_agents.v2.demo.api:app','--host','0.0.0.0','--port','%PORT%'
if not exist "!BACKEND_EXE!" (
    set BACKEND_EXE=uv
    set BACKEND_ARGS='run','python','-m','uvicorn','mfg_agents.v2.demo.api:app','--host','0.0.0.0','--port','%PORT%'
)
powershell -NoProfile -Command "Start-Process -FilePath '!BACKEND_EXE!' -ArgumentList !BACKEND_ARGS! -WorkingDirectory '%CD%' -WindowStyle Minimized -RedirectStandardOutput '%CD%\logs\api.log' -RedirectStandardError '%CD%\logs\api.err.log'"
call :ready 30

:tunnel
rem ---------- [5/6] named tunnel ----------
echo.
echo === [5/6] Cloudflare named tunnel ===
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | findstr /I /C:"cloudflared.exe" >nul
if not errorlevel 1 (
    echo       cloudflared already running - reusing it.
    goto :verify
)

echo       starting ^(minimized window "mfg-tunnel %DOMAIN%"^) ...
del /q "%TLOG%" >nul 2>nul
start "mfg-tunnel %DOMAIN%" /min "%CFEXE%" tunnel --config "%CFG%" --no-autoupdate --logfile "%TLOG%" run
call :wait_tunnel 40

:verify
rem ---------- [6/6] public self-check ----------
echo.
echo === [6/6] Verifying public URL ===
call :wait_public 20

echo.
echo ============================================================
if defined PUBLIC_OK (
    echo   PUBLIC ACCESS READY
) else (
    echo   STARTED, BUT PUBLIC CHECK DID NOT PASS YET
)
echo.
if defined PUBLIC_OK (
    echo     https://%DOMAIN%/            ^(302 to /demo-ui/^)
)
echo     Local  : http://127.0.0.1:%PORT%/demo-ui/
echo.
echo   Tunnel : minimized window "mfg-tunnel %DOMAIN%"  ^(leave it open^)
echo   Backend: minimized window "mfg-api %PORT%"
echo   Logs   : logs\tunnel-hulkai.log   logs\api.log
echo   Stop   : scripts\stop_public.bat
echo   Config : deploy\hulkai-cn\tunnel\config.yml
if not defined PUBLIC_OK (
    echo.
    echo   Checklist when the public URL fails:
    echo     1. Cloudflare zone active? ^(not "Pending Nameserver Update"^)
    echo     2. .tools\cloudflared.exe tunnel list  - is "hulkai" healthy?
    echo     3. curl https://%DOMAIN%/api/v2/health  - see the error text
)
echo ============================================================
echo.

if defined DS_KEY (
    echo [ok] DS_KEY present in this shell - LLM reachable.
) else (
    echo [WARN] DS_KEY is NOT set in this shell.
    echo        The demo UI loads, but every LLM answer will fail.
    echo        Fix: add DS_KEY to .env, or set it as a user env var.
)
echo.

if defined PUBLIC_OK if not defined MFG_NO_PAUSE start "" "https://%DOMAIN%/"
if not defined MFG_NO_PAUSE pause
exit /b 0

rem ---------- subroutines ----------
:check_health
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/v2/health' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){exit 0}}catch{exit 1}" >nul 2>nul
exit /b %errorlevel%

:ready
set /a n=0
:ready_loop
call :check_health
if not errorlevel 1 (
    echo       backend healthy.
    exit /b 0
)
set /a n+=1
if !n! geq %1 (
    echo [WARN] backend not healthy after ~%1*2s - check logs\api.log
    exit /b 1
)
ping -n 3 127.0.0.1 >nul 2>nul
goto :ready_loop

:port_open
powershell -NoProfile -Command "if(Test-NetConnection -ComputerName '127.0.0.1' -Port %1 -InformationLevel Quiet -WarningAction SilentlyContinue){exit 0}else{exit 1}" >nul 2>nul
exit /b %errorlevel%

:wait_port
set /a n=0
:wait_port_loop
call :port_open %1
if not errorlevel 1 (
    echo       port %1 ready.
    exit /b 0
)
set /a n+=1
if !n! geq %2 (
    echo [WARN] port %1 not reachable after ~%2*2s - continuing anyway.
    exit /b 1
)
ping -n 3 127.0.0.1 >nul 2>nul
goto :wait_port_loop

:wait_tunnel
set /a n=0
:wait_tunnel_loop
findstr /C:"Registered tunnel connection" "%TLOG%" >nul 2>nul
if not errorlevel 1 (
    echo       edge connection registered.
    exit /b 0
)
set /a n+=1
if !n! geq %1 (
    echo [WARN] no "Registered tunnel connection" yet - see logs\tunnel-hulkai.log
    exit /b 1
)
ping -n 3 127.0.0.1 >nul 2>nul
goto :wait_tunnel_loop

:wait_public
set /a n=0
:wait_public_loop
curl.exe -s -f -o NUL --max-time 12 "https://%DOMAIN%/api/v2/health" >nul 2>nul
if not errorlevel 1 (
    echo       https://%DOMAIN%/api/v2/health  ^-^>  200 OK
    set PUBLIC_OK=1
    exit /b 0
)
set /a n+=1
if !n! geq %1 (
    echo [WARN] https://%DOMAIN%/api/v2/health did not answer 200.
    exit /b 1
)
ping -n 4 127.0.0.1 >nul 2>nul
goto :wait_public_loop
