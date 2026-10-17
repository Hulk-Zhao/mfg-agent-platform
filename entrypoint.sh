#!/bin/bash
# V3 演示入口：初始化演示库 → 写入知识库种子 → 启动 FastAPI（uvicorn）
set -e

echo "[entrypoint] initializing demo SQLite database..."
python -m mfg_agents.common.init_db || echo "[entrypoint] init_db skipped (already initialized?)"

echo "[entrypoint] seeding ChromaDB failure cases (best-effort)..."
python -m mfg_agents.common.seed || echo "[entrypoint] seed skipped"

echo "[entrypoint] starting FastAPI (mfg_agents.v2.demo.api) on :8004..."
exec python -m uvicorn mfg_agents.v2.demo.api:app \
    --host 0.0.0.0 \
    --port 8004
