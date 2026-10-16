#!/bin/bash
# 等待 MySQL 就绪后再启动 Streamlit
set -e

echo "[entrypoint] waiting for MySQL at ${MYSQL_HOST}:${MYSQL_PORT}..."
until python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${MYSQL_HOST}', ${MYSQL_PORT}))
    sys.exit(0)
except Exception:
    sys.exit(1)
"; do
  sleep 2
done
echo "[entrypoint] MySQL is up."

# 首次启动时初始化知识库
python -c "from mfg_agents.knowledge.seed import run_seed; run_seed()" || echo "[entrypoint] seed skipped (chroma not ready?)"

echo "[entrypoint] starting Streamlit..."
exec streamlit run src/mfg_agents/ui/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true
