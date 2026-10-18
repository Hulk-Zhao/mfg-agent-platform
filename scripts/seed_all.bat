@echo off
rem ============================================================
rem  mfg-agent-platform - 一键播种（制造知识 + 企业制度 + 业务库）
rem
rem  前置：uv 与 Python 3.11+；走 Milvus 时先起 docker-compose.infra.yml。
rem  幂等：全部使用 upsert / INSERT OR IGNORE，可重复执行。
rem
rem  用法：scripts\seed_all.bat
rem ============================================================
cd /d "%~dp0\.."
setlocal

if not exist .env copy .env.example .env

echo === [1/3] 制造知识 -> failure_cases（RAG 向量库） ===
uv run python -m mfg_agents.common.seed
if errorlevel 1 echo [WARN] 制造知识播种失败（Milvus 未就绪？看上方输出）。

echo === [2/3] 企业制度 -> enterprise_faq（Milvus） ===
uv run python -m mfg_agents.common.kbqa_seed
if errorlevel 1 echo [WARN] 企业制度播种失败（Milvus 未就绪？看上方输出）。

echo === [3/3] SQLite 业务库（含 kbqa_policy 结构化镜像） ===
uv run python -m mfg_agents.common.init_db
if errorlevel 1 echo [WARN] 业务库初始化失败。

echo.
echo 完成。MySQL 的 kbqa_policy 行由
echo   docker compose -f docker-compose.infra.yml up -d
echo 首次卷初始化装载（已有卷需 down -v 重灌）。
pause
endlocal
