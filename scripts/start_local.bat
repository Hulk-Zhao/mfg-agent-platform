@echo off
rem mfg-agent-platform - local start (V3 demo: FastAPI + SSE, port 8004)
rem Requires: uv (https://docs.astral.sh/uv/) and Python 3.11+
cd /d "%~dp0\.."

if not exist .env copy .env.example .env

echo === [1/3] Installing dependencies (first run) ===
uv sync
if errorlevel 1 exit /b 1

echo === [2/3] Initializing demo database and knowledge base ===
uv run python -m mfg_agents.db.init_db
uv run python -m mfg_agents.knowledge.seed

echo === [3/3] Starting FastAPI demo ===
echo   Local:   http://127.0.0.1:8004/demo-ui/
echo   Network: http://<your-LAN-IP>:8004/demo-ui/   (bound to 0.0.0.0)
echo   Public:  double-click scripts\start_public.bat for an internet URL
uv run python -m uvicorn mfg_agents.v2.demo.api:app --host 0.0.0.0 --port 8004

pause
