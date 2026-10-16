# mfg_agent_platform.v2 — 可接入框架

V2 把 V1 原型中"硬编码的场景知识"抽取为声明式配置，并提供三类标准接入契约：
**新系统按契约实现即可接入，零改框架代码**。

```
v2/
├── contracts/            # 接入契约层
│   ├── im_gateway.py     #   IMGateway ABC + InboundMessage / ReplyCard
│   ├── adapters/wecom.py #   企业微信适配器（验签/AES 解密/回复 XML）
│   ├── data_source.py    #   DataSource ABC + SQLiteDataSource + Registry
│   └── scenario.py       #   ScenarioMeta + ScenarioRegistry（YAML 场景注册）
├── core/                 # 编排内核
│   ├── router.py         #   TwoLevelRouter：L0 规则快路径 / L1 LLM 闭集分类 / 降级
│   ├── yaml_dag.py       #   YamlDagExecutor：YAML → LangGraph 子图
│   ├── shared_context.py #   SharedContext：只增不改 + source 溯源
│   ├── domain_state.py   #   DomainState + reducer（按域合并，域间不覆盖）
│   ├── node_registry.py  #   域 Agent handler 注册表（复用 V1 资产）
│   ├── orchestrator.py   #   V2Orchestrator：统一入口
│   └── events.py         #   请求级 context-var 事件队列（修 D6 并发串话）
├── services/             # 能力服务（无状态，被所有域组复用）
│   ├── text2sql.py       #   Text2SQLService：指标库→注册查询→明细探查 八道防线
│   ├── notification.py   #   NotificationService：P0/P1/P2 分级通知
│   └── workflow.py       #   WorkflowEngine：状态机 + SLA + HITL 待办队列
├── scenarios/            # 声明式场景（line_stop_v2.yaml = V1 line_stop 等价迁移）
├── data/                 # queries.yaml（注册查询）+ metrics/（指标定义即代码）
└── demo/                 # cli.py / api.py / im_simulator.py（offline 可跑）
```

## 快速开始（offline 可跑，无需 Ollama）

```bash
# 1) CLI demo：全链路事件流 + 简报 + provenance + degraded 标记
uv run python -m mfg_agents.v2.demo.cli "A型号在SMT-1停线，何时复线？"

# 2) FastAPI demo：统一入口 + SSE 事件流 + 企业微信回调
IM_TOKEN=QDG6eK \
IM_AES_KEY=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG \
IM_APPID=wx5823bf96d3bd56c7 \
uv run python -m uvicorn mfg_agents.v2.demo.api:app --port 8004

# 3) IM 模拟器：离线构造真实格式回调 → 全链路 → 消息卡片
uv run python -m mfg_agents.v2.demo.im_simulator

# 测试
uv run pytest tests/ -q
```

---

## 接入指南一：接入新 IM（实现 IMGateway）

以"接入钉钉"为例，三步零改框架代码：

**第 1 步：实现契约**（`src/mfg_agents/v2/contracts/adapters/dingtalk.py`）

```python
from ..im_gateway import IMGateway, InboundMessage, ReplyCard

class DingTalkAdapter(IMGateway):
    def verify_callback(self, payload, headers=None) -> bool:
        # 钉钉签名算法（HMAC-SHA256）
        ...
    def receive(self, payload) -> InboundMessage:
        # 解析钉钉消息体 → InboundMessage(user_id, conversation_id, text, channel="dingtalk")
        ...
    def reply(self, msg, card) -> Any:
        # 回调窗口内被动回复（钉钉 text/markdown/actionCard）
        ...
    def push(self, user_id, card) -> None:
        # 主动推送（工作流通知 / HITL 催办走这里）
        ...
```

**第 2 步：注册适配器**（可选，工厂化）

```python
# demo/api.py 中按渠道分发
ADAPTERS = {"wecom": WeComAdapter, "dingtalk": DingTalkAdapter}
```

**第 3 步：挂载回调路由**（参照 `demo/api.py` 的企微端点）

```python
@app.post("/api/v2/im/dingtalk/callback")
async def dingtalk_callback(request: Request):
    adapter = DingTalkAdapter(token=..., app_secret=...)
    payload = await request.json()
    if not adapter.verify_callback(payload):
        raise HTTPException(status_code=403)
    msg = adapter.receive(payload)
    result = get_orchestrator().handle(msg.text, user_ctx, sink=QueueSink())
    return adapter.reply(msg, _build_card(result, msg))
```

> HITL 卡片按钮回写：平台侧提供 `WorkflowEngine.advance(task_uid, action)`
> （action ∈ start/approve/reject/escalate/close），适配器只需把卡片按钮的
> key 映射为 action 调用即可（见 `services/workflow.py`）。

## 接入指南二：接入新数据源（实现 DataSource）

以"接入 MySQL 只读副本"为例：

```python
import sqlalchemy
from mfg_agents.v2.contracts.data_source import DataSource, DataSourceRegistry

class MySQLDataSource(DataSource):
    name = "erp_mysql"

    def __init__(self, url: str, queries: dict[str, str]):
        self._engine = sqlalchemy.create_engine(url)   # 只读账号
        self.queries = queries

    def list_tables(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(sqlalchemy.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"))
        return [r[0] for r in rows]

    def run_query(self, name: str, params: dict | None = None) -> list[dict]:
        sql = self.queries[name]                       # 注册查询（参数化）
        with self._engine.connect() as conn:
            return [dict(r) for r in conn.execute(
                sqlalchemy.text(sql), params or {})]

    def execute_readonly_sql(self, sql: str, params=None, row_cap=200) -> list[dict]:
        # 调用方保证已过 Text2SQL 审核链；此处仍加 LIMIT 兜底
        ...
```

注册（框架启动处或自建装配代码）：

```python
registry = DataSourceRegistry()
registry.register(MySQLDataSource("mysql+pymysql://ro@erp/mfg", queries),
                  default=True)     # default=True 成为默认源
```

随后 Text2SQLService 的全部通道（指标库/注册查询/明细探查）自动面向新源工作；
表级权限在 `services/text2sql.py` 的 `TABLE_ACL` 登记新表（fail-closed）。

## 接入指南三：新增场景（写 YAML）

新场景 = 新 YAML + 复用现有 Agent 节点，**零代码**。把文件放进
`src/mfg_agents/v2/scenarios/`，启动时 ScenarioRegistry 自动注册
（解析失败 fail-fast，不会静默跳过）。

```yaml
key: material_shortage_v2        # 场景唯一键
name: 缺料预警联动
version: 1
domains: [supply_chain, manufacturing]
triggers:
  - {type: keyword, words: [缺料, 断料, 齐套]}     # L0 快路径
  - {type: im_message}
  - {type: event, topic: mfg.material.shortage.predicted}
entry: scan
outputs: [shortage_list, response_tickets]
steps:
  - {id: scan, type: agent, agent: material_control, timeout_s: 30, on_error: degrade}
  - id: evaluate                    # fan_out：四域并行评估
    type: fan_out
    join: all
    branches:
      - {id: planning, type: agent, agent: planning, timeout_s: 30, on_error: degrade}
      - {id: procurement, type: agent, agent: procurement, timeout_s: 30, on_error: degrade}
  - {id: join, type: fan_in}
  - {id: dispatch, type: workflow, workflow: shortage_response_wf,
     title: 缺料处置工单, assignees: [采购, 计划], sla_hours: 4}
  - {id: urgent_po, type: hitl, agent: hitl_urgent_po,
     prompt: 加急采购决策（金额>5万需采购总监确认）,
     approver_roles: [采购总监],
     actions: [{key: approve, label: 同意加急}, {key: reject, label: 驳回}]}
```

五步节点类型：`agent`（调域 Agent）/ `fan_out`（并行多分支，支持 `condition`+`else`
条件跳转）/ `fan_in`（汇聚）/ `workflow`（调工作流引擎）/ `hitl`（IM 卡片人工确认，
进入 waiting_hitl，绝不自动放行）。节点级韧性：`timeout_s` + `on_error: degrade|abort`，
任一分支 degraded 不阻塞整体。

新 Agent handler 注册一次即可被所有场景引用（见 `core/node_registry.py`）：

```python
def material_control(task, ctx, sink) -> dict:
    qr = runtime.text2sql.query_registered("shortage_scan", {}, task.user_ctx)
    return {"domain": "supply_chain", "status": "ok",
            "payload": {"shortage_list": qr.rows,
                        "provenance": [qr.to_dict()]},
            "summary": f"发现 {len(qr.rows)} 项缺口物料"}
```

---

## 设计纪律（不可妥协）

1. **LLM 不碰确定性流程**：审批/通知/升级/工单状态只走 WorkflowEngine；
   LLM 节点只产"建议"进入 task 节点。
2. **数据质量先于智能能力**：指标库优先 → 注册查询 → 明细探查；
   超范围返回 `fallback="manual_ticket"`（人工兜底是设计的一部分）；
   所有结论带 provenance（SQL 原文 + 命中行 + 耗时）。
3. **可审计**：SharedContext 只增不改，每条事实带 source；
   事件枚举与 V1 前端完全兼容（agent_start/tool_start/llm_start/final/error）。
4. **offline 可跑**：LLM 不可用自动降级（路由规则化 / FA 与报告模板化），
   全链路仍可演示——这是硬性验收项。
5. **V1 零破坏**：V1 代码与五个演示脚本未改动；等价行为由
   `tests/test_v1_regression.py` 与 `tests/v2/test_e2e_v2.py` 守护。
