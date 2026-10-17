@echo off
rem mfg-agent-platform - Docker up (build and start)
cd /d "%~dp0\.."

echo === Build and start containers ===
docker compose up -d --build

echo.
echo === Status ===
docker compose ps

echo.
echo UI: http://localhost:8004/demo-ui/
pause
