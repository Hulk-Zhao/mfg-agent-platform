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

echo === [1/4] 制造知识 -> failure_cases（RAG 向量库） ===
uv run python -m mfg_agents.common.seed
if errorlevel 1 echo [WARN] 制造知识播种失败（Milvus 未就绪？看上方输出）。

echo === [2/4] 企业制度 -> enterprise_faq（Milvus） ===
uv run python -m mfg_agents.common.kbqa_seed
if errorlevel 1 echo [WARN] 企业制度播种失败（Milvus 未就绪？看上方输出）。

echo === [3/4] 清除 kbqa 答案缓存（旧语料的缓存答案会以假乱真） ===
uv run python -c "import os;from dotenv import load_dotenv;load_dotenv();u=(os.getenv('REDIS_URL') or '').strip();import redis;c=redis.Redis.from_url(u,decode_responses=True,socket_connect_timeout=1);ks=list(c.scan_iter('mfg:kbqa:*'));print('kbqa cache cleared:',c.delete(*ks) if ks else 0)" 2>nul
if errorlevel 1 echo [INFO] Redis 未就绪，跳过（缓存 30 分钟自然过期）。

echo === [4/4] SQLite 业务库（含 kbqa_policy 结构化镜像） ===
uv run python -m mfg_agents.common.init_db
if errorlevel 1 echo [WARN] 业务库初始化失败。

echo.
echo 完成。MySQL 的 kbqa_policy 行由
echo   docker compose -f docker-compose.infra.yml up -d
echo 首次卷初始化装载（已有卷需 down -v 重灌）。
pause
endlocal
