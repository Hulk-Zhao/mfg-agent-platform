# mfg_agent_platform.v2 — 可接入框架

V2 把 V1 原型中"硬编码的场景知识"抽取为声明式配置，并提供三类标准接入契约：
**新系统按契约实现即可接入，零改框架代码**。

```
v2/
├── base/                 # 基础设施层
│   └── logger.py         #   get_logger / configure（薄封装，不替换现有 logging）
├── contracts/            # 接入契约层
│   ├── im_gateway.py     #   IMGateway ABC + InboundMessage / ReplyCard
│   ├── adapters/wecom.py #   企业微信适配器（验签/AES 解密/回复 XML）
│   ├── data_source.py    #   DataSource ABC + SQLiteDataSource + Registry
│   ├── scenario.py       #   ScenarioMeta + ScenarioRegistry（YAML 场景注册）
│   └── vector_store.py   #   [新] VectorStore ABC + VectorHit + ChromaVectorStore（零依赖）
├── prompts/              # [新] 提示词集中管理
│   ├── routing.py        #   L1 路由分类 prompt（DOMAIN_GROUPS + 模板）
│   ├── fa.py / report.py #   FA 提示词 / 报告提示词 + FORBIDDEN_WORDS + 3 个模板函数
│   └── text2sql.py       #   明细探查 prompt（复用 V1 SCHEMA_PROMPT）
├── intent/               # [新] 意图/策略分离骨架（对齐 query_classifier + strategy_selector）
│   ├── classifier.py     #   IntentClassifier ABC + RuleLLMClassifier（包装现 Router）
│   └── strategy.py       #   StrategySelector ABC + DefaultStrategySelector
├── core/                 # 编排内核
│   ├── router.py         #   TwoLevelRouter：L0 规则快路径 / L1 LLM 闭集分类 / 降级
│   ├── yaml_dag.py       #   YamlDagExecutor：YAML → LangGraph 子图
│   ├── shared_context.py #   SharedContext：只增不改 + source 溯源
│   ├── domain_state.py   #   DomainState + reducer（按域合并，域间不覆盖）
│   ├── node_registry.py  #   域 Agent handler 注册表（薄装配层：5 个工厂）
│   ├── orchestrator.py   #   V2Orchestrator：统一入口
│   └── events.py         #   请求级 context-var 事件队列（修 D6 并发串话）
├── agents/               # [新] 业务域模块化（各域独立文件）
│   ├── quality.py        #   SQE 判定 / QA 8D 牵头 / PQE 过程 / SQE 供应商
│   ├── supply_chain.py   #   采购/计划/物控/仓库ATP/物流/业务/窗口
│   ├── manufacturing.py  #   维保建议
│   ├── engineering.py    #   工程（设计根因/参数/备件）——degrade 模板 + data_gap
│   ├── workflow_nodes.py #   通用工作流节点工厂
│   ├── hitl.py           #   HITL kinds 工厂（restart/eta/disposition/d8_close/...）
│   └── report.py         #   通用报告 handler 工厂（按场景选模板）
├── services/             # 能力服务（无状态，被所有域组复用）
│   ├── text2sql/         #   Text2SQLService（包化分层：service/auditor/masker/libraries）
│   ├── notification.py   #   NotificationService：P0/P1/P2 分级通知
│   └── workflow.py       #   WorkflowEngine：状态机 + SLA + HITL 待办队列
├── scenarios/            # 声明式场景（line_stop_v2 + 4 新场景）
├── data/                 # queries.yaml（注册查询）+ metrics/（指标定义即代码）
└── demo/                 # cli.py / api.py / im_simulator.py / ui（offline 可跑）
```

> **场景**：`line_stop_v2`（停线应急）、`incoming_quality_v2`（来料异常 S2）、
> `customer_complaint_v2`（客诉 8D S3）、`delivery_status_v2`（出货风险 S4）、
> `equipment_warning_v2`（设备预警 S8）。

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

新场景 = 新 YAML + **复用已注册的 handler key**（agent / workflow / hitl），
**不改框架代码**。把文件放进 `src/mfg_agents/v2/scenarios/`，启动时
ScenarioRegistry 自动注册（解析失败 fail-fast，不会静默跳过）。

> **关键前提**：YAML 里的 `agent:` / `workflow:` / `hitl:` 取值必须是**已注册的 key**
> （见 `core/node_registry.py`）。本示例全部复用已注册 key——`business` /
> `material_control` / `planning` / `procurement`（agent）、`delivery_risk_flow`
> （通用 workflow 工厂）、`hitl_delivery_commit`（通用 HITL 工厂，prompt/审批人/按钮
> 均由 step 参数覆盖）。**可直接拷贝运行**（已在离线环境实测通过）。
> **若要语义专属的新 workflow id 或新 HITL kind**，需在 `node_registry` 注册一次
> （+1 行，例如 `handlers["shortage_response_wf"] = make_workflow_handler(engine, notifier)`），
> 那就不再是"纯零代码"，请如实说明。

```yaml
key: material_shortage_v2        # 场景唯一键
name: 缺料预警联动
version: 1
domains: [supply_chain, manufacturing]
triggers:
  - {type: keyword, words: [缺料, 断料, 齐套]}     # L0 快路径
  - {type: im_message}
entry: intake
outputs: [shortage_list, response_tickets]
steps:
  - {id: intake, type: agent, agent: business, label: 业务Agent · 意图识别, timeout_s: 10, on_error: degrade}
  - {id: scan, type: agent, agent: material_control, label: 物控 · 缺料扫描, timeout_s: 30, on_error: degrade}
  - id: evaluate                    # fan_out：多域并行评估
    type: fan_out
    join: all
    branches:
      - {id: planning, type: agent, agent: planning, timeout_s: 30, on_error: degrade}
      - {id: procurement, type: agent, agent: procurement, timeout_s: 30, on_error: degrade}
  - {id: join, type: fan_in}
  - {id: dispatch, type: workflow, workflow: delivery_risk_flow,   # 已注册的通用 workflow 工厂
     title: 缺料处置工单, assignees: [采购, 计划], notify_level: P1, sla_hours: 4, timeout_s: 15, on_error: degrade}
  - {id: urgent_po, type: hitl, agent: hitl_delivery_commit,        # 已注册的通用 HITL 工厂
     prompt: 加急采购决策（金额>5万需采购总监确认）,
     approver_roles: [采购总监],
     actions: [{key: approve, label: 同意加急}, {key: reject, label: 驳回}],
     timeout_s: 10, on_error: degrade}
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

## 安全边界实现要点（Round 2 加固后）

- **SQL 审核链用 sqlglot 真解析器**（非正则打补丁）：表名提取覆盖
  FROM/JOIN/CTE/子查询/UNION、括号紧贴、反引号/双引号标识符、逗号隐式
  join；**解析失败即 fail-closed 拒绝**（`SQLAuditError`）。
- **脱敏基于 AST 投影别名映射**：`SELECT supplier AS s` 的别名 `s`
  会解析回源列 `supplier` 后按角色掩码（`FieldMasker.mask(rows, ctx, sql)`）。
- **user_ctx 入口校验**：roles 为 None/不可迭代、dept 非字符串 →
  直接拒绝查询（fail-closed，不逃逸 TypeError）。

## 扩展位（本次仅留位，不接线，零新依赖）

- **意图识别演进位** `intent/`：`IntentClassifier` / `StrategySelector` ABC +
  `RuleLLMClassifier`（内部直接调用现 `TwoLevelRouter`，行为等价）。迁移路径：
  `Router` 改为 `classifier.classify → selector.select`，`RouteResult` 收敛为
  `IntentResult` 别名；再下一步分类器可替换为独立小模型/分类服务。本次**不改**
  Router 运行路径（保证回归基线）。
- **RAG 向量库演进位** `contracts/vector_store.py`：`VectorStore` ABC +
  `ChromaVectorStore` 适配器（包装 `tools/rag_tools.py`，同 collection、同语义，
  行为不变）。接 Milvus 只需实现
  `contracts/adapters/milvus.py::MilvusVectorStore(VectorStore)`，**零框架改动**。
  混合检索（向量 + BM25 + RRF 融合）落点 `services/rag/hybrid.py`（本次仅设计，不引
  `pymilvus`/`rank_bm25`）。
- **提示词集中** `prompts/`：全部 prompt/禁词表/模板集中管理，字符串逐字节复制；
  `tests/v2/test_prompts_golden.py` 金标准护不变量。

## 演示简化（明确标注）

- **8D 不建表**：客诉 8D 的 D1-D8 由 `agents/quality.py::make_qa_lead` **规则模板
  确定性合成**，由 `capa_flow` 工单 context 承载（不新增 DDL）。生产需持久化
  D1-D8 步骤（见"已知限制"）。
- **工程域数据缺口**：设计根因 / 参数基线 / 备件库存当前无数据源 → `degrade` 模板 +
  `data_gap` 标记，前端显式渲染"该数据当前无来源"（**不编造**）。

## 已知限制（记录在案，生产前需处理）

1. **企微回调无重放防护**：同一合法签名重复提交会被再次接受（企微官方
   被动回调同样依赖 5s 窗口）。生产接入需在 API 层补 timestamp 窗口验重
   + nonce 缓存。
2. **`:memory:` 数据源/工作流库**：SQLite 内存库按"进程内共享连接"支持
   （StaticPool 语义，`SQLiteDataSource` 按源名共享、`WorkflowEngine`
   用持久单连接），仅用于测试/嵌入式场景；生产用文件库或 MySQL。
3. **OTD 演示口径**：`data/metrics/otd.yaml` 以 `shipment.eta` 为客户
   要求日（演示库无真实要求日/发货日字段），对外上线前必须替换为真实
   字段并在指标库升版。
4. **8D 进度未持久化**：D1-D8 目前由模板合成，仅存于 `capa_flow` 工单 context；
   生产需建 `capa_8d_step` 表持久化每步状态/责任人/证据（U1）。
5. **工程/备件/在途数据源缺失**：设计根因、工艺参数基线、备件库存无数据源，
   降级为模板 + `data_gap`；生产接入 PLM/EAM/ERP 后替换（U2）。
6. **意图/向量库未接线**：`intent/` 与 `VectorStore` 本次仅骨架，
   Router 与 `fa` 仍走原路径；切换列为后续独立需求（U3/U4）。
7. **`VectorStore` 签名与 Chroma 适配器不完全一致（H-4）**：`VectorStore` 各方法
   带 `collection` 参数，但 `ChromaVectorStore` 目前**恒用单 collection**
   （`failure_cases`，行为与 `rag_tools` 一致）而忽略该参数。接入 Milvus 适配器
   之前需**先收敛签名**（多集合语义落地），本轮不改实现。
