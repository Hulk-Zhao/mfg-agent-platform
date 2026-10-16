@echo off
chcp 65001 >nul
title mfg-agent-platform - Start
cd /d "%~dp0\.."

echo === [1/3] 启动 MySQL + ChromaDB ===
docker compose up -d mysql chroma

echo === [2/3] 安装依赖（首次） ===
uv sync

echo === [3/3] 启动 Streamlit UI ===
uv run streamlit run src/mfg_agents/ui/app.py

pause
