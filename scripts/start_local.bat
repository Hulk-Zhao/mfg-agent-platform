@echo off
rem ============================================================
rem  mfg-agent-platform - 本地开发启动（V3 demo: FastAPI + SSE, 端口 8004）
rem
rem  前置：uv（https://docs.astral.sh/uv/）与 Python 3.11+
rem  中间件：VECTOR_BACKEND=milvus 时需先起 docker-compose.infra.yml
rem          （MySQL 3307 / Milvus 19531 / Redis 6380）
rem ============================================================
cd /d "%~dp0\.."
setlocal enabledelayedexpansion

set PORT=8004
set MILVUS_PORT=19531

if not exist .env copy .env.example .env

rem ---- 读取 .env：向量库后端与 Milvus 端口 ----
set VECTOR_BACKEND=chroma
for /f "usebackq tokens=1,2 delims==" %%a in (`findstr /B /C:"VECTOR_BACKEND=" .env 2^>nul`) do set VECTOR_BACKEND=%%b
for /f "usebackq tokens=1,2 delims==" %%a in (`findstr /B /C:"MILVUS_URI=" .env 2^>nul`) do (
    for /f "tokens=3 delims=:" %%p in ("%%b") do set MILVUS_PORT=%%p
)

echo ============================================================
echo   mfg-agent-platform local start   ^(VECTOR_BACKEND=%VECTOR_BACKEND%^)
echo ============================================================

rem ---------- [1/5] 中间件 ----------
echo === [1/5] Middleware ^(MySQL / Milvus / Redis^) ===
if /i not "%VECTOR_BACKEND%"=="milvus" (
    echo       skipped ^(VECTOR_BACKEND is not milvus^)
    goto :deps
)
where docker >nul 2>nul
if errorlevel 1 (
    echo [WARN] docker not found - skip middleware startup.
    goto :deps
)
docker compose -f docker-compose.infra.yml up -d
call :wait_port %MILVUS_PORT%

:deps
rem ---------- [2/5] 依赖同步 ----------
echo === [2/5] Syncing dependencies ===
uv sync
if errorlevel 1 (
    echo [ERROR] uv sync failed.
    pause
    exit /b 1
)

rem ---------- [3/5] 演示库 + 知识库 ----------
echo === [3/5] Initializing demo database and knowledge base ===
uv run python -m mfg_agents.common.init_db
if errorlevel 1 echo [WARN] init_db failed - see output above.

set SEED_OK=1
if /i "%VECTOR_BACKEND%"=="milvus" (
    call :port_open %MILVUS_PORT%
    if errorlevel 1 set SEED_OK=0
)
if "!SEED_OK!"=="1" (
    uv run python -m mfg_agents.common.seed
    if errorlevel 1 echo [WARN] seed failed - see output above.
) else (
    echo [WARN] Milvus port %MILVUS_PORT% unreachable - skipping knowledge seed.
    echo        Fix: docker compose -f docker-compose.infra.yml up -d
)

rem ---------- [4/5] 端口占用检查 ----------
echo === [4/5] Checking local port %PORT% ===
call :check_health
if not errorlevel 1 (
    echo       Service already running on port %PORT% - nothing to do.
    echo       UI: http://127.0.0.1:%PORT%/demo-ui/
    start "" "http://127.0.0.1:%PORT%/demo-ui/"
    pause
    exit /b 0
)
set PORTBUSY=
for /f "tokens=*" %%l in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do set PORTBUSY=1
if defined PORTBUSY (
    echo [ERROR] Port %PORT% is held by another process:
    netstat -ano ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"
    echo         Free it with scripts\stop_public.bat and retry.
    pause
    exit /b 1
)

rem ---------- [5/5] 启动 FastAPI ----------
echo === [5/5] Starting FastAPI demo ===
echo   Local:   http://127.0.0.1:%PORT%/demo-ui/
echo   Network: http://^<your-LAN-IP^>:%PORT%/demo-ui/   ^(bound to 0.0.0.0^)
echo   Public:  double-click scripts\start_public.bat for an internet URL
uv run python -m uvicorn mfg_agents.v2.demo.api:app --host 0.0.0.0 --port %PORT%

pause
exit /b 0

rem ---------- subroutines ----------
:check_health
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/v2/health' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){exit 0}}catch{exit 1}" >nul 2>nul
exit /b %errorlevel%

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
if !n! geq 24 (
    echo [WARN] port %1 not reachable after ~60s - continuing anyway.
    exit /b 1
)
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul 2>nul
goto :wait_port_loop
