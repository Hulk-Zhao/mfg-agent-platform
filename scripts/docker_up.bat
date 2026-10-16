@echo off
chcp 65001 >nul
title mfg-agent-platform - Docker Up
cd /d "%~dp0\.."

echo === Build & Start all containers ===
docker compose up -d --build

echo.
echo === Status ===
docker compose ps

echo.
echo UI: http://localhost:8501
pause
