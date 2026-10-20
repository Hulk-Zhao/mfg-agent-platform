# mfg-agent-platform

制造业多智能体协同平台。18 个智能体（17 个领域智能体 + 1 个办公智能体）、7 大协同场景，LangGraph 子图编排、人工在环会签、Text2SQL 只读治理、本地增强 Agentic RAG 案例检索、A2A / MCP 双协议接入；另设**生态链演示门户**——一条主线订单驱动六个场景屏，全链路只读、数据合成可复现。

[![tests](https://github.com/Hulk-Zhao/mfg-agent-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Hulk-Zhao/mfg-agent-platform/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![tests](https://img.shields.io/badge/tests-1896%20offline-brightgreen)

<p float="left">
  <img src="docs/images/screen-dashboard.png" width="24.5%" />
  <img src="docs/images/screen-chat.png" width="24.5%" />
  <img src="docs/images/screen-hitl.png" width="24.5%" />
  <img src="docs/images/screen-spc.png" width="24.5%" />
</p>

**解决的问题**：制造企业的异常响应卡在三处——进度散落在各部门系统与微信群里，跨部门同步以天计；取数靠"问人、导 Excel、写 SQL"；停线、缺料、客诉的跨部门取证靠打电话，闭环周期长。平台把"提问 → 路由 → 多域并行取证 → 工单 → 人工会签 → 对客报告"收敛成一条可审计的链路，每一步都有事件流、SQL 溯源和权限校验。

---

## 近期更新

**现场看板推送闭环：客诉 → 责任工段 → 现场可操作**

- 客诉 8D 场景新增 `dispatch_site` 节点：归因顺序固定为「显式工段 → 产线前缀 → 批次反查」，三层都判不出即返回 `data_gap` 转人工分派；归因结果同时追加为 8D 工单责任人，现场才有"归属"。
- 新增治理库表 `site_board_push` 与现场看板读 / 回写面（`GET /api/v2/site-board`、`POST …/{push_id}/ack`、`POST …/{push_id}/feedback`）：`pushed → acked → closed` 状态机、非法流转 409、闭环结论必填 400、重推只刷新内容不打回现场进度；三端点共用 `data_scope.yaml` 的 `site_board` 裁决（角色集合与制造看板同源）与 `rows[].customer` 脱敏（非对客岗位看「客户 A」）；门禁顺序 令牌 401 → 岗位 403 → 参数 400 → 不存在 404 → 状态机 409。
- 预警中心新增**第五类「现场推送」**：前四类由只读业务库机械推导，它读治理库（业务库故障时仍如实展示）；现场岗位在卡片上直接「现场接收 / 闭环处置」（必填结论弹窗），状态与筛选片计数即时同步。
- 「**工段 → 现场群**」映射目录 `v2/data/im_groups.yaml`：结构在 YAML、凭据在 env（`IM_GROUP_KEY_<SLUG>` / `IM_GROUP_MEMBERS_<SLUG>`），工段白名单加载期校验（**推错群比不推更贵**）；投递逐条记账（`pushed` / `skipped` / `failed`），未配置即如实记账、不静默丢弃。
- 企微**应用消息**发送链路 `WeComPushClient`（`gettoken` → `message/send`，token TTL 缓存、`errcode≠0` 抛错、凭据不齐构造期报错）：配齐 `IM_CORP_ID` / `IM_AGENT_ID` / `IM_CORP_SECRET` 后，群内「责任人」由"只留痕"变为真实 @ 投递。

**本批一并落地的其他改动（按模块）**

- **看板与数据**：SPC 看板升级（PRD / 设计 / 类图 / 时序 + `test_spc_dashboard.py`）、制造与订单看板数据扩充与明细下钻（新增 `data/seed_*` 与生成工具）、柏拉图双窗筛选、来料 Top10、标准工时三级明细。
- **案例域**：Agentic RAG 升级（`case_agentic` / `case_qa_llm` / `case_relation` / 本体缺口登记）、案例屏智能体 UI 与评测用例。
- **预警与摘要**：预警中心聚合服务（`services/alerts.py`）、日报摘要 `services/digest.py`。
- **问数**：看板上下文带入 `services/board_qa.py`（点击 KPI / 明细即携带口径跳转问数）。
- **办公**：OA 服务工单（IT 报修 / 账号权限 / 设备领用 / 车位 / 访客车辆 / 公务用车）与附件、飞书后端补齐抽象方法。
- **前端**：深色驾驶舱主题 + 明暗 / 主题色个性化切换、老化模式、生态链场景屏智能体回放与知识卡（T16–T20，见下方提交记录）。
- **Text2SQL**：新增 SQL 静态检查 `services/text2sql/linter.py`。

---

## 技术架构

```
                    ┌────────────────────────────────────────────┐
   Web / IM / A2A   │  接入层                                     │
  ─────────────────►│  FastAPI + SSE 事件流 · 企微回调(AES-CBC)    │
                    │  A2A JSON-RPC (Agent Card + message/send)   │
                    └───────────────┬────────────────────────────┘
                                    ▼
                    ┌────────────────────────────────────────────┐
                    │  TwoLevelRouter                            │
                    │  L0 规则快路径 / L1 LLM 闭集分类 / 模糊兜底   │
                    └───────────────┬────────────────────────────┘
                                    ▼
                    ┌────────────────────────────────────────────┐
                    │  场景编排（YAML 声明式，7 个场景）             │
                    │  加载期编译为 LangGraph StateGraph 子图       │
                    │  节点：agent / fan_out / fan_in /            │
                    │        workflow / hitl                      │
                    │  每节点 timeout_s + on_error: degrade       │
                    └──┬──────────┬──────────┬───────────────────┘
                       ▼          ▼          ▼
                 品质域(QMS)  供应链(SCM)  设备域(EAM)   ← 多域并行取证
                       └──────────┴──────────┘
                                    ▼
                    ┌────────────────────────────────────────────┐
                    │  WorkflowEngine（状态机 + SLA + 分级通知）    │
                    │  HITL 会签：propose → N×cosign → approve    │
                    └────────────────────────────────────────────┘
```

**编排内核**。场景不写代码：YAML 声明节点与依赖，加载期编译成 LangGraph 子图执行。总控（`V2Orchestrator`）有明确边界纪律——不直接查库（必须经 Text2SQLService）、不直接发通知（必须经 NotificationService）、不做审批判断（必须经 WorkflowEngine）。

**分级 LLM**。L1 小模型跑高频低敏任务（意图分类、SQL 初版），L2 大模型跑分析报告。提供方可插拔：Ollama（本地）、任意 OpenAI 兼容网关（DeepSeek / 智谱 / Kimi…）。可用性探针带 TTL 缓存，LLM 不可用时路由走规则、结论用模板、显式标 `degraded`——数字始终取自 SQL，不编造。

**契约层**。外部系统按契约接入，零框架改动：

| 契约 | 方法 | 内置实现 |
|---|---|---|
| `DataSource` | list_tables / run_query / execute_readonly_sql | SQLite（只读连接 `mode=ro`，写操作在驱动层即被拒绝） |
| `VectorStore` | upsert / query | Milvus standalone（当前部署：BGE-M3 稠密+稀疏混合召回 + BGE-Reranker-v2-M3）；代码默认 ChromaDB（零依赖本地持久化），`VECTOR_BACKEND` 一键切换 |
| `IMGateway` | 消息收发 + 回调验签 | 企业微信自建应用（AES-CBC + 签名验证） |
| `NotificationChannel` | send(level, targets, title, body, context) | 日志留痕（内存 outbox，demo 断言用）/ 企微推送 / **现场看板**（落 `site_board_push`）三路并联，`context` 为可选投递上下文（承载业务引用与落库幂等键） |

新数据源实现三个方法、注册进 `DataSourceRegistry`，即刻出现在 `/api/v2/datasources` 就绪度总览——健康状态、表清单、注册查询数。

**治理层**。声明式权限目录（对标 Huly controlled-documents 范式）：9 个 HITL 槽位 × 3 个动词（propose / cosign / approve）= 27 条权限、32 个治理角色，全部声明在 YAML（第 9 槽位 `order_review` 为接单评审会签，propose=业务 / cosign=工程+计划 / approve=业务经理）。`PermissionRegistry` 构造期自检：Agent 身份只允许 `*.propose`，持有 `*.approve` 直接启动失败。会签一人一签、并发幂等。

**现场看板推送（客诉 → 责任工段）**。客诉不停在"谁来牵头"，而是先**判责任工段再推**：归因顺序固定为「显式工段 → 产线前缀（`SMT-`/`DIP-`/`PACK-`/`ASSY-` → 工段）→ 批次反查（取该批次不良量最大的行所属工段，只读 `mode=ro` 读业务库）」，三层都判不出来就返回 `data_gap` 转人工分派——**判不出工段就不硬推，推错工段比不推更贵**。推送行走同一个 `NotificationChannel` 契约（`context` 承载客诉单号 / 批次，用于落库引用与幂等键），落进与工单同库的 `site_board_push` 表，便于把现场处置回流到 8D 关闭确认；现场侧状态机 `pushed → acked → closed`，**非法流转直接拒绝**（不做"尽力而为"），**重推（重试 / 重放）只刷新内容、不打回现场已推进的状态**。通道与日志留痕并联挂载，且只对**工段级 target** 落库：target 是岗位角色（如「质量总监」）时如实跳过并记账——工段才是现场看板的对象。客诉 8D 场景已在四域汇聚与 8D 工单之间挂 `dispatch_site` 节点，归因结果同时回写为工单责任人（工段追加进 `assignees`），因此该工段现场看板会收到两条：客诉处置令（引用客诉单号）与对应的 8D 整改工单通知。现场看板有独立的读 / 回写面：`GET /api/v2/site-board`（另有 `ack` / `feedback` 两个回写端点）— 三个端点共用一个裁决（`data_scope.yaml` 的 `site_board`，角色集合与制造看板同源）与同一条脱敏规则（`rows[].customer`：只有对客岗位看得到客户真名）；门禁顺序为 令牌 401 → 岗位 403 → 参数 400 → 不存在 404 → 状态机 409，现场接收与闭环都按令牌身份留痕（闭环结论必填，空则 400）。**载体是预警中心**：`GET /api/v2/alerts` 把它作为第五类「现场推送」呈现（前四类由只读业务库机械推导，它读治理库，业务库故障时仍如实展示），现场岗位在卡片上直接接收 / 闭环，状态与筛选片计数即时同步——现场推送就此从"能调用"变成"现场可操作"。**IM 触达走「工段 → 现场群」映射目录**（`v2/data/im_groups.yaml`）：结构（哪个工段对应哪个群、要 @ 谁）声明在 YAML，**凭据不入库**——群机器人 webhook key 与责任人 userid 由 `IM_GROUP_KEY_<SLUG>` / `IM_GROUP_MEMBERS_<SLUG>` 注入；加载期逐个校验工段必须来自 `site_board.STAGES`（**推错群比不推更贵**，写错工段名即启动失败）。投递逐条记账：命中群走群机器人 webhook（真实可发）、未配凭据如实记 `skipped` 并给出理由（只落站内，不假装发出去了）、配了责任人 + 企业应用凭据则**经应用消息 API 真实 @ 到人**（`WeComPushClient`：`gettoken` → `message/send`，token 带 TTL 缓存、`errcode≠0` 抛错；凭据不齐才记 `failed` 并说清原因）——三路（日志留痕 / 站内看板 / 现场群）并联挂载，同一条通知一次分发。企业应用凭据（`IM_CORP_ID` / `IM_AGENT_ID` / `IM_CORP_SECRET`，前者缺省回落回调侧的 `IM_APPID`）同样只从环境变量注入。

**待办可见性同源于权限目录**。`/workflow/pending` 不再接受任何形式的口头身份：角色一律由 Bearer 令牌裁决，`role` 参数只能收窄不能扩宽；可见范围 = 我持有权限的 HITL 槽位（`mine` 可动作 / `domain` 域内只读）。**行政人事这类无治理职责的岗位拿到的是空队列**——不是"没资格看"，而是"本来就没有你的活"，两种状态在响应里可区分。详情接口同理，跨域返回 404 而非 403（403 会确认工单存在）。MCP / A2A 通道走同一套判据，工具参数里的 `roles` 无法覆盖真实身份。

**Text2SQL 八道防线**。仅 SELECT、多语句拦截、sqlglot AST 提取表名、表级 ACL（fail-closed）、行数上限、按角色脱敏、provenance 全溯源、人工兜底。数字从哪张表哪条 SQL 来，全程可追溯。

**SPC 统计内核**。Cpk / Pp / Ppk / 柏拉图由 `core/spc.py` 纯函数计算，不经过 LLM；无测量数据时输出 `data_gap=True` 并强制走人工确认——缺数据也要人拍板，而不是编一个结论。

**本地增强 Agentic RAG · 制造案例检索**。独立案例域（contracts / services / security / agents 共 20+ 模块），一句话概括：**授权发生在检索之前，无引用不输出**。

- **两段式安全检索**：服务端按身份编译过滤条件（ABAC：显式 deny > 全局硬门禁 > 维度 AND > RBAC > 默认拒绝）并**下推向量库**——过滤在召回前生效，不是召回后筛选；返回前再用关系库 ACL 做第二次授权（独立代码路径）。候选不足时**绝不放宽过滤重试**：`hits == 0` 返回空 + `hidden_count`，宁可少给，不给不该给的。
- **最小泄漏面**：无权案例统一 `404 CASE_NOT_FOUND_OR_NOT_VISIBLE`，不泄漏存在性；`hidden_count` 只在候选不足时才计算，避免"结果够了还告诉你还有多少"。
- **无引用不输出**：每条结论必须挂 `(case_id, chunk_id, content_version)` 三元组，引用校验器回查知识层，版本不一致即丢弃；全部断言被丢弃就明示拒答。**引用合法 ≠ 答的是所问**：相关度低于阈值直接拒绝——问"食堂几点开饭"不会得到一段锡膏印刷的答案。演示态用确定性模板作答（全链路本地可跑、可复现），接 LLM 只需注入同一个 `Answerer` 协议。
- **本体增强**：受控词表（缺陷模式 / 根因 / 对策，受控编码 DM-SMT / RC-EQP…）把"同一现象的不同叫法"归一，再做同义扩展与混合检索；重排按 状态权重 × 新鲜度 × 有效性 确定性计算，同案例去重 ≤ 2 块。
- **向量后端可插拔**：一套 `CaseIndex` 契约三个实现——内存（默认）/ ChromaDB / Milvus，`CASE_VECTOR_BACKEND` 环境变量切换，零代码改动。
- **案例域独立权限表**：六个检索动词（检索命中 / 预览 / 读全文 / 引用 / 导出 / 外发）各自声明可见状态白名单——未结案与已撤权永不进索引；**导出默认拒绝**（可看 ≠ 可带走，走申请审批），**外发分享恒定拒绝**。Agent 不自动批准访问申请：`CaseAccessAgent` 只按受控词表把申请路由到职能部门并留痕，推断不出就明说，路由错了比不推更糟。
- **审计链可对账**：每次检索/问答落审计（主体 / 动作 / 命中数 / 查询哈希 / 过滤哈希），只记哈希不记正文；提供一键校验，逐环核对哈希与计数。

**协议接入**。两套标准协议都是最小自实现（零第三方依赖）：

- **MCP Server**：stdio JSON-RPC 2.0，MCP 2024-11-05 规范工具子集，`ToolRegistry` 单一事实源，按域分治（data / asset / quality / workflow / knowledge，单 server ≤ 15 工具），任何 MCP 客户端可挂载。
- **A2A**（Agent-to-Agent，Linux 基金会规范）：`GET /.well-known/agent.json` Agent Card + `POST /a2a` 的 `message/send` / `tasks/get`。外部智能体消费平台能力拿到的是结构化 Task + Artifact，且治理链（ACL / 脱敏 / 会签）与 HTTP 端点同一套——A2A 不是绕过治理的后门。

**数据与缓存**。SQLAlchemy 2.0 抽象：本地开发默认 SQLite（零依赖），生产通过 `DB_URL` 切 MySQL 8.0。向量检索双路召回 + 重排：BGE-M3（dense + sparse）加 BGE-Reranker-v2-M3，后端可换 Milvus standalone（当前部署）/ ChromaDB（默认零依赖）。Redis 承担会话上下文缓存、查询结果缓存与单 IP 限流，连接失败自动降级为无缓存。

**认证与岗位分层**。演示门户 31 个岗位免密选择登录，写接口凭 HMAC 签名的岗位访问令牌（8 小时 TTL）；`AUTH_SECRET` 未配置时生成进程级随机密钥并告警。

分两层，且**两层都在服务端**（菜单只挡入口，不挡 `curl`）：

| 层 | 落点 | 行为 |
|---|---|---|
| 导航 | `app.js` 的 `NAV_ITEMS[].roles` | 总经理 12 屏全开，HR 只见 7 屏且**没有审批入口**，一线岗位不见经营看板；侧边栏按「运营视图 → 智能协同 → 流程治理 → 复盘沉淀」四段排列，深链接与手输 hash 同样回落 |
| 待办 | `v2/services/workflow_scope.py` | 只看「我的活」+「我治理域内待观察」；无治理职责的岗位是**空队列**而非 403，跨域工单详情返回 404 |
| 数据 | `v2/data/data_scope.yaml` | 四个 `/dashboard/*` + `/dashboard/drill` 按岗位裁决：无令牌 401、无权限 **403**（不返回 200 空数据）；订单看板客户身份按岗位脱敏，仓库/采购/物控看到「客户 A / 客户 B」并失去客户维度下钻键 |

两份目录在加载期做构造校验（角色必须在 32 角色权限目录内、脱敏路径必须可解析、`api.py` 新增端点必须登记），写错一个字母启动即失败。导航的 `roles` 另有一条测试与 `permissions.yaml` **交叉校验**——「审批确认中心」的可见岗位必须等于「持有 HITL 槽位权限的岗位」，两边不会各自漂移。

前端不做权限判断，只如实呈现服务端的裁决结果（含「已脱敏 N 项」角标）。**演示数据一律显式标注**：后端在线时简报不再拼演示预警（否则人事会看到一条不存在的订单风险），首页指标卡带「演示配置」角标——它显示 OTD 92.4%，而真实链路按 seed 数据算出的是 71.4%，两套口径必须说清楚。

**健康检查**。`/api/v2/health` 是结构化依赖探针（业务库 / 治理库 / Redis / 向量库 / LLM），返回 `ok` / `degraded` 与逐项明细，前端顶栏据此显示绿 / 黄 / 红三色；探针**不触发编排器初始化**，可供 Docker `HEALTHCHECK` 高频调用。

**前端**。原生 HTML/CSS/JS，零构建零 npm 依赖，挂载于 FastAPI，SSE 实时事件流驱动。内置可拖动的**悬浮帮助助手**（`float-help.js`，随场景切换会话、答疑静态预置不调后端、选项过多自动出滚动条），演示门户另有「生态链场景」分组承载六个场景屏。

## 部署形态

SQLite + 本地向量库（ChromaDB 默认 / 可接 Milvus）是**本地开发的零依赖默认值**，不是平台的边界——**业务读路径**（DataSource / VectorStore 契约）按部署形态切换，应用代码零改动；**治理写路径**（工单 / 会签 / SLA）当前仍固定 SQLite，见下表说明：

| 形态 | 业务库 | 治理库（工单/会签/SLA） | 向量库 | 缓存 | 适用 |
|---|---|---|---|---|---|
| 本地开发 | SQLite（默认） | 同一 SQLite 文件 | ChromaDB（默认）/ 可接 Milvus | 无（自动降级） | 跑通全链路、跑测试 |
| Docker 单容器 | 同上（`app_data` 卷） | 同上 | 同上 | 同上 | 快速演示 |
| 中间件栈 | MySQL 8.0 | **仍为 SQLite（`WORKFLOW_DB`，单文件单实例）** | Milvus standalone + BGE-M3/Reranker | Redis 7 | 单实例生产 |

> **为什么治理库不跟着切 MySQL**：`WorkflowEngine` 直连 `sqlite3`，未接入 SQLAlchemy 抽象（1025 行，重写风险高于收益）。因此 `WORKFLOW_DB` **不继承 `DB_URL`**——`DB_URL` 指向 MySQL 时它自动回落本地 SQLite 文件；显式传入非 SQLite 地址会在启动时抛 `WorkflowBackendError`（fail-fast，不允许把 URL 当文件路径静默建库）。多实例部署前必须先抽 `WorkflowRepository` 契约，见「生产化清单」。

中间件栈一条命令拉起（含健康检查、端口避让）：

```bash
docker compose -f docker-compose.infra.yml up -d   # MySQL :3307 / Milvus :19531 / Redis :6380
```

再在 `.env` 里设 `DB_URL=mysql+pymysql://...`、`VECTOR_BACKEND=milvus`、`MILVUS_URI=...`、`REDIS_URL=...` 即完成切换，应用代码零改动（**业务读路径**；治理库见上表说明，需另设 `WORKFLOW_DB` 指向 SQLite）。

### 应用跑在容器里时：两套地址视角

`.env` 是**宿主机视角**写的。应用一旦跑进容器，`localhost:19531` 指向的就是容器自己，连不到中间件。`docker-compose.yml` 已按此给了容器视角的默认值（与 `OLLAMA_BASE_URL` 同一套路），并实测容器内可访问宿主机映射的 19531 / 6380 / 3307：

| 变量 | 作用 | 默认值 |
|---|---|---|
| `CONTAINER_MILVUS_URI` | 覆盖容器内的向量库地址 | `http://host.docker.internal:19531` |
| `CONTAINER_REDIS_URL` | 覆盖容器内的缓存地址 | `redis://host.docker.internal:6380/0` |
| `CONTAINER_DB_URL` | 覆盖容器内的业务库地址 | 空 = 回落默认 SQLite（单容器演示） |

**生产形态建议用组合启动**——两个 compose 在同一目录（同一 project），网络自动合并，app 与中间件直接走服务名：

```bash
docker compose -f docker-compose.yml -f docker-compose.infra.yml up -d
# 此时设：CONTAINER_MILVUS_URI=http://milvus:19530
#      CONTAINER_REDIS_URL=redis://redis:6379/0
#      CONTAINER_DB_URL=mysql+pymysql://root:demo123@mysql:3306/mfg_agent
```

组合启动时无需 `host.docker.internal`，也没有宿主机端口映射这一跳。

## 快速开始

```bash
git clone https://github.com/Hulk-Zhao/mfg-agent-platform.git
cd mfg-agent-platform
cp .env.example .env           # LLM 相关全部留空 = 离线降级模式，可跑通全链路
uv sync
uv run python -m mfg_agents.common.init_db
uv run uvicorn mfg_agents.v2.demo.api:app --port 8004
```

打开 http://127.0.0.1:8004/demo-ui/ ，选岗位进入。Docker：`docker compose up -d --build`。

不起 UI：`uv run python -m mfg_agents.v2.demo.cli "A型号在SMT-1停线，何时复线？"`，事件流与 provenance 直接打印。

## 演示场景

| 场景 | 示例问题 | 链路特点 |
|---|---|---|
| 停线应急 | A型号在SMT-1停线，何时复线？ | 三域并行取证 + FA 评级 + 复线决策会签 + 对客 ETA 会签 |
| 来料异常 | 今天锡膏批次有问题 | 供应链与品质联合判定，退货/换货/特采走会签 |
| 客户投诉 | 华为投诉BATCH-A-1008 | 品质追溯 + 8D 结构化报告 + 关闭会签 + **归因工段现场看板推送**（8D 工单责任人按工段动态追加） |
| 出货风险 | A型号能发多少货 | 制造加仓储 ATP 核算，交期承诺会签 |
| 设备预警 | SMT-1设备参数异常吗 | 设备日志 + FA 历史案例匹配，停机窗口会签 |
| 订单变更影响 | 订单交期提前两周是否可行 | 制造与供应链联合评估产能与交期，变更承诺会签 |
| SPC 过程异常 | 关键尺寸 Cpk 掉到 0.8 了，要不要介入 | 能力指数判定 + 不良柏拉图取证 + 处置会签 |

同一问题不同岗位可见范围不同：PQE 查来料时供应商字段脱敏，采购可见原文——表级 ACL 与字段脱敏由登录角色驱动。

## 生态链演示（主线订单 · 六场景屏）

面向公网 / 客户演示的**一条主线订单故事**：固定随机种子（20261019）合成 15 类对象 / 43 事件，主线 `SO-DEMO-001` 串起「接单 → 立项 → 阶段门 → 批量与 ECN → 出货追溯 → 采购执行」六屏，另有 ECN 换料（支线 A）与客诉 8D（支线 B）两条支线。数据由服务端装载层提供（`/api/v2/demo/objects/{name}`、`/api/v2/demo/timeline`），屏内**不落散数**，全部经 `SceneKit.story()` 现取。

| 场景屏 | 演示主线 |
|---|---|
| S1 · 接单评审 | ATP 初评三来源（在库 / 在制 / 安全库存）+ 报价区间三分量现算对账 + 接单评审一次会签（真实 HITL 工单） |
| S2 · 立项 | 立项材料自动汇总 + 相似历史案例检索 + 立项会签 |
| S3 · 阶段门 | EVT / DVT / PVT 记分卡四维现算 + 门禁机械判定 + 会签留痕 |
| S4 · 批量监控与 ECN | SPC 判异处置会签 + 支线 A 换料影响面四象限（在制 / 库存 / 在途 / 未发） |
| S5 · 出货与质量追溯 | 批次 → 工单 → 工序 → 设备 → 当班 SVG 追溯图谱 + 同批库存位置 + 8D 时间线 |
| S6 · 采购执行 | 交期确认 + 缺料预警 + 自动催料待办 + 延期升级链 |

**五访客角色**（`gm / biz / rd / qa / mfg`）登录各见不同屏与数据范围，可见性由 `roles` 机械判定；`cs / hr / fin` 等岗位不出现「生态链场景」分组。**框架级演示纪律**：所有场景屏首帧必须带「演示数据」徽标——`SceneKit.installBadgeGuard()` 包装 `SceneRegistry.register`，漏挂即拒绝渲染并 `console.error`；所有接口响应带 `X-Demo-Mode` / `X-Demo-Label` 头（纯 ASGI `DemoMarkerMiddleware`，对 SSE 零影响）；另有静态层隔离自检 `tools/demo_gen/check_isolation.py`（连接串 / 凭据 / 真实企业名词三类，扫演示实例范围内文件）。

**悬浮帮助助手**（`float-help.js`）：可拖动、位置存 `localStorage`（越界自动钳回视口，窗口缩放不丢）；随 `state.screen` 切换场景即开新会话，点击预置问题后答案流式输出；答疑为**静态预置、不调后端**，与帮助中心同纪律——改屏内交互 / 链路 / 权限口径时必须同步改其 KB。交互细节：常见问题超过约 6 条时选项区出现独立滚动条（滚轮下滑，滚到头不穿透背后页面）；流式输出期间上滑回看不被强制拽回底部；输出被打断（换问题 / 切场景 / 关面板）自动摘除光标；拖到一半松手不算点击，不会意外开合面板。

## 门户一览

| 制造案例检索（本地增强 Agentic RAG） | 订单履约看板 |
|---|---|
| ![制造案例检索](docs/images/screen-cases.png) | ![订单履约看板](docs/images/screen-orders.png) |
| **制造综合看板** | **预警中心** |
| ![制造综合看板](docs/images/screen-mfgboard.png) | ![预警中心](docs/images/screen-alerts.png) |
| **报告中心** | **企业制度问答（RAG 结论带出处）** |
| ![报告中心](docs/images/screen-reports.png) | ![企业制度问答](docs/images/screen-kbqa.png) |
| **OA 协同（会议室 / 订餐 · 高影响先人工确认）** | **数据速查（库存只读 · 岗位约束）** |
| ![OA 协同](docs/images/screen-oa.png) | ![数据速查](docs/images/screen-data.png) |
| **登录选岗（31 岗位 5 组）** | **帮助中心（架构与职责说明）** |
| ![登录选岗](docs/images/screen-login.png) | ![帮助中心](docs/images/screen-help.png) |

## LLM 接入

提供方通过 `LLM_PROVIDER` 显式指定，留空自动解析；所有提供方走同一 `ChatModel` 抽象，按节点分级分配模型与思考档位。

| 提供方 | 配置 | 说明 |
|---|---|---|
| Ollama（本地） | `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | 零成本跑通，默认 `qwen3.5` 系列 |
| OpenAI 兼容网关 | `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | DeepSeek、智谱、Kimi、vLLM 自部署等任何兼容端点 |
| 全部留空 | — | 离线降级：规则路由 + 模板结论，标 `degraded`，链路完整可演示 |

## 测试

1896 条离线测试（探针恒 False、不发外网请求），CI 在 Python 3.11 / 3.12 / 3.13 三个版本执行：

```bash
uv run pytest tests/ -q
```

覆盖：治理链（权限目录自检、会签幂等、越权拦截）、Text2SQL 防线（红队用例）、场景端到端、看板下钻、SPC 内核、**现场看板推送**（工段归因三层顺序与漂移守卫 / 客诉分派全链路 / 通道只认工段 target / 幂等重推不打回现场进度 / 两类 `data_gap` 兜底 / 客诉 8D 场景分派节点端到端 / 接口层 401·403·400·404·409 与客户名按岗位脱敏 / 预警中心第五类「现场推送」的状态等级映射、前端类型对回与写操作入口 / **工段→现场群映射**（目录自检拒绝非白名单工段、凭据 env 覆盖、投递逐条记账且缺凭据不假装已发、三路并联端到端）/ **企微应用消息**（gettoken→message/send 报文逐字段对回、token TTL 缓存、errcode≠0 抛错、凭据不齐构造期报错、装配网关按凭据齐缺决定——见 `tests/v2/test_wecom_app_push.py`）——见 `tests/v2/test_site_board.py`、`tests/demo/test_alerts_api.py`、`tests/v2/test_im_groups.py`）、**案例域安全检索**（两段式授权 / 引用校验 / RBAC-ABAC 策略 / 审计链对账 / 限流 / 顶部统计卡同源 / 总经理跨部门通行）、MCP / A2A 协议、企微加解密、飞书适配、**生态链演示**（屏 / 数据 / 组件三契约、六场景屏数字现取、演示徽标强制、演示标识中间件、接单评审工单化、隔离自检——见 `tests/demo/`）；以及**架构守卫**（治理库拒绝非 SQLite、方言判定不乱猜、健康探针不抛异常、导航按岗位收敛——见 `tests/v2/test_架构守卫.py`）、**数据范围守卫**（看板按岗位裁决 / 403 不降级成演示数据 / 脱敏与下钻键成对处理——见 `tests/v2/test_数据范围.py`）、**待办可见性守卫**（令牌为准 / 跨域 404 / 三条通道同一口径——见 `tests/v2/test_待办可见性.py`）和 **FAQ 前置层守卫**（L1 双闸 / 意图路由分流 / how_to 让位 RAG——见 `tests/v2/test_kbqa_faq_tier.py`、`tests/v2/test_kbqa_intent.py`）。

## 项目结构

```
mfg-agent-platform/
├── src/mfg_agents/common/        # 基础设施：配置 / LLM 工厂(分级) / RAG / Text2SQL / 建库种子
├── src/mfg_agents/v2/            # 主链路
│   ├── contracts/                #   接入契约：DataSource / VectorStore / IMGateway / 权限
│   ├── data/                     #   声明式治理数据：permissions.yaml / queries.yaml / data_scope.yaml
│   ├── core/                     #   编排内核：两级路由 / YAML→LangGraph 子图 / 共享上下文 / SPC 内核 / 缓存
│   ├── agents/                   #   域 Agent（quality / supply_chain / engineering / hitl / office / agentic）
│   ├── intent/                   #   意图分类器与路由策略
│   ├── services/                 #   Text2SQL 服务链 / 工作流引擎 / 通知（日志 · IM · 现场看板）/ 看板 / 认证 / 数据范围 / 待办可见性 / 数据访问 / 案例检索问答 / 飞书日历
│   ├── security/                 #   案例域安全：RBAC / ABAC 策略编译与求值 / 过滤翻译 / 限流
│   ├── scenarios/                #   8 个场景 YAML（7 个对话路由 + 接单评审会签；新场景零代码新增）
│   ├── mcp_tools/                #   ToolRegistry + 自实现 MCP server（stdio JSON-RPC）
│   ├── a2a.py                    #   A2A 协议（Agent Card / message/send / tasks/get）
│   └── demo/                     #   FastAPI 入口 / CLI / 演示门户（纯静态零构建 · 生态链场景屏 scenes/ + 悬浮帮助助手）
├── data/                         # 演示数据种子 SQL + 知识库 JSONL + 案例种子 + 制度语料（kbqa_corpus）+ 生态链演示数据（demo_storyline：15 类 jsonl / 43 事件 + 六屏数据契约）
├── deploy/                       # Milvus standalone 内嵌 etcd 配置
├── tests/                        # 1896 条离线测试（治理 / 红队 / 端到端 / 协议 / 架构守卫 / 数据范围 / 待办可见性 / 案例域 / FAQ 前置层 / 现场看板推送 / 预警中心 / 工段→现场群映射 / 企微应用消息）；tests/demo/ 为生态链演示冒烟与六屏契约
├── tools/                        # 数据扩充 / 阈值校准 / 案例评测 / 演示数据生成与隔离自检（demo_gen）
├── docs/                         # 架构 / 接口 / HITL 设计 / 案例助手 / 优化设计（images/ 为门户截图）
└── scripts/                      # Windows 一键启动脚本（本地 / Docker / 公网隧道 / 健康看门狗）
```

## 生产化清单

当前为单实例演示实现，治理层已落地（声明式权限目录 + 启动自检 + 一人一签幂等）。上线前待办：

- **治理库切换 MySQL**：`WorkflowEngine` 当前固定 SQLite（`WORKFLOW_DB`，单文件单实例）。已加启动期 fail-fast 拒绝非 SQLite 地址，避免把 MySQL URL 当文件路径静默建库；要落 MySQL 需先抽 `WorkflowRepository` 契约，再实现 SQLAlchemy 后端。
- **会签并发与幂等**：进程内 `RLock` 串行化需升级为数据库级——签名拆独立表 + 唯一索引 `(task_uid, role_key, actor, verb)` 幂等。**与上一条应一并做**（多实例的前提是签名落库）。
- **看板数据范围**：四个 `/dashboard/*` 端点已接声明式数据范围目录（`v2/data/data_scope.yaml`）：按岗位 allow + 字段级脱敏 + 下钻同源裁决，未登记看板 fail-closed。剩余缺口是**行级**（同一看板内按组织/产线过滤到本人辖区）——当前是"看到全厂该岗位该看的部分"，生产前需把 `data_scope.yaml` 从角色维度扩展到组织维度。
- **待办数据范围**：`/workflow/pending` 与 `/workflow/{task_id}` 已按 HITL 槽位裁决（令牌为准、`role` 只能收窄、跨域 404、MCP 同口径）。剩余缺口与看板同类——**行级**：同一槽位内按组织归属过滤（华东/华南质量负责人各看各的）；另外**未纳管的历史工单**没有槽位可判，统一进"无主池"（对有治理职责的岗位只读可见），需要一次工单归属回填才能消掉。
- **现场看板推送的剩余缺口**：链路、接口、前端承载、IM 映射与**应用消息发送**都已落地（客诉 8D 场景 `dispatch_site` 归因工段 → 推送该工段现场看板 → 8D 工单责任人按工段追加 → `GET /api/v2/site-board` 读、`ack` / `feedback` 回写并留痕 → 预警中心第五类「现场推送」现场可直接接收与闭环 → 「工段 → 现场群」映射负责 IM 触达：群机器人 webhook 发群、`WeComPushClient` 走 `gettoken`/`message/send` @ 责任人）。还剩两件：① **真实租户联调**（企业微信后台建自建应用、配 CorpID/AgentId/Secret 与应用可见范围、IP 白名单——代码侧凭据齐即真发，`errcode≠0` 会抛错并记 failed，属部署动作）；② **行级归属**（同一工段内按组织/产线缩到本人辖区，与四个看板同类缺口）。
- **A2A 任务持久化**：`tasks/get` 结果当前进程内保留 1 小时，生产需持久化。
- **公网访问认证**：读接口（看板 / 下钻 / 待办 / 工单详情）均已 fail-closed 要求令牌；剩余的"链接即权限"在于**登录本身免密**（31 个岗位任选即入），对外正式提供前必须替换为 SSO。
- **企微回调重放防护**：需补 timestamp 窗口验重 + nonce 缓存。
- **超时代签**：字段位已预留（`on_behalf_of` / `overdue_signoff`），入口未开放。

## 文档

- [docs/README.md](docs/README.md) — 文档索引
- [docs/ARCHITECTURE-V2.md](docs/ARCHITECTURE-V2.md) — 总架构（契约层 / 编排内核 / 治理层设计详述）
- [docs/V2-接口文档.md](docs/V2-接口文档.md) — API 文档（统一入口 / SSE / HITL 回写 / 错误码）
- [docs/V2-HITL-角色协作设计.md](docs/V2-HITL-角色协作设计.md) — 人工在环与会签设计
- [docs/case-assistant-demo-readme.md](docs/case-assistant-demo-readme.md) — 制造案例检索（本地增强 Agentic RAG）交付说明
- [docs/case-assistant-ontology-vector-auth-architecture.md](docs/case-assistant-ontology-vector-auth-architecture.md) — 案例域本体 / 向量授权架构
- [docs/adr/ADR-001-企业问答助手检索架构.md](docs/adr/ADR-001-企业问答助手检索架构.md) — 企业制度问答三层检索架构决策
- [docs/edrag-optimization/](docs/edrag-optimization/) — 制度问答 / 意图路由 / FAQ 边界优化设计（DESIGN / DELIVERY / TASK）
- [docs/产品技术文档-mfg-agent-platform-v1.0.html](docs/产品技术文档-mfg-agent-platform-v1.0.html) — 产品技术文档（自包含 HTML）
- [docs/制造生态链-产品价值速览-v1.0.html](docs/制造生态链-产品价值速览-v1.0.html) — 制造生态链产品价值速览（高管版）

## License

[MIT](LICENSE)
