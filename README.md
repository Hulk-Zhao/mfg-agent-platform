# mfg-agent-platform

制造业多智能体协同平台：基于 **LangGraph** 编排 8 个专业 Agent，覆盖停线应急、来料异常、客户投诉、出货风险、设备预警 5 个业务场景，自动生成对客脱敏报告。

## 架构

```
Streamlit UI
     │
Business Agent (意图识别 / 场景路由 / 参数抽取)
     │
     ├── Manufacturing Agent (MES: OEE/产出/停线)
     ├── Quality Agent (QMS: 不良分布)
     ├── Supply Chain Agent (SCM: 来料/COA)
     ├── Equipment Agent (EAM: 设备日志/参数)
     ├── Warehouse Agent (WMS/TMS: 库存/发货)
     └── FA Agent (RAG历史案例 + LLM评级/ETA)
     │
Report Agent (对客脱敏 vs 内部简报)
     │
MySQL (9张模拟表)  +  ChromaDB (历史案例库)
```

## Agent 职责

| Agent | 系统 | 职责 |
|---|---|---|
| Business | - | 意图识别、场景路由、任务拆解、调度 |
| Manufacturing | MES | Text-to-SQL 查 OEE、产出达成、停线确认 |
| Quality | QMS | 不良分布、主导缺陷识别 |
| Supply Chain | SCM | 来料批次、COA、供应商状态 |
| Equipment | EAM | 设备告警、参数异常 |
| Warehouse | WMS/TMS | 可用库存、在途发货 |
| FA | RAG+LLM | 历史案例匹配、异常评级(A/B/C/D)、复线ETA |
| Report | - | 对客报告自动脱敏，内部简报并行输出 |

## 快速开始（本地 UV）

```powershell
# 0. 复制环境变量
copy .env.example .env

# 1. 安装依赖
uv sync

# 2. 启动 MySQL 与 ChromaDB
docker compose up -d mysql chroma

# 3. 初始化知识库
uv run python -c "from mfg_agents.knowledge.seed import run_seed; run_seed()"

# 4. 启动 UI（或双击 scripts\start_local.bat）
uv run streamlit run src/mfg_agents/ui/app.py
```

浏览器打开 http://localhost:8501

## 一键 Docker

```powershell
copy .env.example .env
# 双击 scripts\docker_up.bat，或：
docker compose up -d --build
```

## LLM 配置

- 本地 Ollama（复用宿主机已下载模型）：`LLM_PROVIDER=ollama`，默认 `qwen3.5:9b`
- 云端兼容接口（DeepSeek/智谱）：`LLM_PROVIDER=openai`，填 `OPENAI_API_KEY`

## 演示场景

1. **停线应急**："A型号在SMT-1线停线，何时复线？" → 三并行取证 + FA评级
2. **来料异常**："今天锡膏批次有问题" → 供应链+品质
3. **客户投诉**："华为投诉BATCH-A-1008" → 品质追溯
4. **出货风险**："A型号能发多少货" → 制造+仓储
5. **设备预警**："SMT-1设备参数异常吗" → 设备+FA

## 技术栈

Python 3.11+ · LangGraph · LangChain · MySQL · ChromaDB · Streamlit · Docker Compose · UV
