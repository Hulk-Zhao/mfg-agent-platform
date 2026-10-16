# V2 QA 验证报告（对抗性验证）

- **验证人**：QA 工程师 严过关（software-qa-engineer）
- **对象**：commit `51c2ff0`（V2 可接入框架，基线 `49dcd34` / tag `v1.0-prototype`）
- **方法**：独立复算 + 红队攻击 + NIST 外部向量锚点 + demo 三件套实测；不信工程师自述，全部结论有测试/命令输出支撑
- **全量结果**：`uv run pytest tests/ -q` → **234 passed / 9 failed / 2 skipped**
  - 工程师既有 112 用例全部通过（V1/V2 零破坏）
  - QA 新增 5 个测试文件共 130 个用例（74 安全红队 + 26 密码学 + 25 HITL/工作流 + 5 事件流 + 3 V1 等价补测），另加 1 个 API E2E 实测脚本
  - 9 个失败**全部是源码缺陷的行为证据**（详见 Bug 清单），非测试自身问题

---

## 智能路由判定

> **路由给：软件工程师（software-engineer）**
> 发现 **5 个源码 Bug**（1 高 / 2 中 / 2 低），均有确定性复现与期望/实际对照。
> QA 自身测试问题 3 处已自行修复（记录在案）。修复后需 Round 2 回归。

| # | 级别 | 位置 | 一句话 |
|---|------|------|--------|
| BUG-1 | **高** | `src/mfg_agents/v2/services/text2sql.py:53-54` | SQLAuditor 表级 ACL 可被 4 种语法变体绕过，实测越权读到数据 |
| BUG-2 | 中 | `src/mfg_agents/v2/services/text2sql.py:170-185` | FieldMasker 按列名精确匹配，`SELECT supplier AS s` 别名绕过脱敏 |
| BUG-3 | 低 | `src/mfg_agents/v2/services/text2sql.py:139` | `user_ctx.roles=null` 时抛 TypeError 而非 fail-closed 的 SQLAuditError |
| BUG-4 | 低 | `src/mfg_agents/v2/services/workflow.py:82` + `db/platform_schema.py` | `sqlite:///:memory:` 不可用：SQLAlchemy 建表与原生 sqlite3 操作两个不同的内存库 |
| BUG-5 | 中 | `src/mfg_agents/v2/core/yaml_dag.py:253-258` + `core/events.py:42-46` | handler 在 ThreadPoolExecutor worker 线程执行，contextvar 队列绑定不传播 → SSE 丢失全部 12 个 tool 级事件 |

---

## A. V1 兼容性真实性

### A.1 `collect_ignore` 裁判：**接受**（但覆盖缺口已由 QA 补齐）

独立阅读 5 个被跳过文件，确认工程师声称属实：

| 文件 | 性质（实测证据） |
|------|------|
| `test_e2e.py` | 顶层直接 `app.invoke()`，无测试函数；V1 图无降级模式，离线跑 FA 节点报 Ollama 502 |
| `test_events.py` | 顶层脚本；同样 `app.invoke()` 依赖真实 LLM |
| `test_chunk.py` | 顶层脚本；`app.stream()` 依赖真实 LLM |
| `test_sql.py` | 顶层脚本；`text_to_sql()` 依赖 Ollama |
| `test_stream.py` | 顶层脚本；`requests` 打 `localhost:8003`，要求服务在运行 |

→ 五个均为**演示脚本而非可收集的真测试**（无 test 函数、依赖外部服务/LLM），`collect_ignore` 处理正当，且文件零改动仍可 `uv run python tests/test_*.py` 手动跑。

`test_v1_regression.py` 等价覆盖评估（四项主覆盖均真实，断言有实质内容）：
- V1 图可编译 ✓（`build_graph().get_graph()`）
- 五场景关键词路由 ✓（逐场景断言 scenario 值）
- SQL 只读守卫 ✓（DDL/DML 逐一 `pytest.raises`，且诚实地记录了 V1 多语句已知边界并指向 V2 补齐测试）
- 域结论结构 ✓（与 V1 演示输出同构的数值断言：OEE 32.1 / 锡膏偏移 77pcs / 3 条告警）

**发现的缺口（QA 已补齐）**：原 `test_events.py`（事件队列 push 行为）与 `test_chunk.py`（stream values 模式）的行为面无等价覆盖。QA 新增 `tests/v2/test_qa_v1_equivalence.py` 补齐，并用 `skipif(llm_available())` 门控——离线跳过（与演示脚本同条件），有 LLM 时执行。**注意：本文件的编写过程本身又反向证明了 collect_ignore 的正当性——V1 图离线跑必 502。**

### A.2 V1 行为零破坏：**确认**

`git diff 49dcd34 51c2ff0 -- src/mfg_agents/agents src/mfg_agents/graph src/mfg_agents/tools src/mfg_agents/api.py src/mfg_agents/db src/mfg_agents/events.py` 逐项审查：

- `tools/llm_factory.py`：仅新增 `tier` 关键字参数（默认 `"L2"` → 沿用原 `settings.OLLAMA_MODEL`，V1 调用点行为逐字节一致）+ `llm_available()` 探针 + import 格式化。**V1 逻辑零改动**。
- `db/platform_schema.py`：新文件（V2 平台表 + 指标种子），不触碰 V1 八张模拟表。
- `agents/ graph/ api.py events.py`：**零 diff**。
- `config.py` / `pyproject.toml`：仅新增 V2 配置项与依赖，V1 默认值全部保留。

结论：**V1 零破坏成立**。

### A.3 "为过而过"测试识别：**未发现实质性 trivial 测试'

通读全部 112 个既有用例，断言均有实质内容（数值/结构/异常类型）。仅 1 处 cosmetic：`tests/v2/test_workflow.py:83` `assert engine.check_sla(task) in (True, False)` 为恒真断言（同文件其余断言有效）。不阻断，建议工程师下次顺手改为 `is True`。

---

## B. 安全边界红队（`tests/v2/test_qa_security.py`，74 用例）

### 攻击矩阵（实测结果）

| 攻击类 | 用例 | 结果 |
|--------|------|------|
| DDL/DML 注入 | DROP/DELETE/UPDATE/INSERT/TRUNCATE/ALTER/CREATE/GRANT/REVOKE/ATTACH/DETACH/PRAGMA/VACUUM/REINDEX/REPLACE + 中文"删除 inventory 表" | ✅ 全部拦截（SQLAuditError），走服务入口返回 `fallback=manual_ticket` 零数据行 |
| 堆叠查询 | `SELECT 1; DELETE...` / `;DROP` / `; ;` / `;--` / 大小写变体 | ✅ 全部拦截（`SELECT 1;;` 被 rstrip 中和为单语句，行为安全——QA 原断言过严，已自行修正） |
| 注释截断/藏毒 | `-- DROP`、`/* DELETE */`、良性尾注释 | ✅ 藏毒拦截；良性注释放行 |
| UNION 探测 | UNION / UNION ALL / 小写 + 越权表 | ✅ 全部命中第二张表 ACL 拦截 |
| 表级权限 | 7 组跨角色越权、未注册表、`sqlite_master`、子查询/CTE/EXISTS/JOIN 内越权表、空 user_ctx | ✅ fail-closed 拦截（roles=None 除外 → BUG-3） |
| **ACL 绕过变体** | `FROM(t)` / `FROM\n(t)` / 反引号 / 双引号 / 逗号隐式 join | ❌ **BUG-1：放行且实测返回越权数据** |
| 敏感字段脱敏 | supplier/unit_price 按角色（含 end-to-end 注册查询） | ✅ 生效（非别名场景） |
| **脱敏别名绕过** | `SELECT supplier AS s` | ❌ **BUG-2：别名原样输出敏感值** |
| 执行层 | mode=ro 拒写/拒建表、行数上限（250 行表→50）、自带大 LIMIT 的 fetchmany 兜底、load_extension 拒绝、缺文件不建空库 | ✅ 全部符合预期 |
| 兜底契约 | 被拦截查询零数据行 / confidence=0 / provenance.blocked | ✅ |

### BUG-1 复现（高危）

```python
from mfg_agents.v2.services.text2sql import SQLAuditor
MFG = {"dept": "manufacturing", "roles": ["manufacturing"]}   # 纯净制造角色
SQLAuditor.audit("SELECT complaint_no FROM(customer_complaint)", MFG)   # 实际：放行；期望：SQLAuditError
SQLAuditor.audit("SELECT complaint_no FROM `customer_complaint`", MFG)  # 实际：放行；期望：SQLAuditError
SQLAuditor.audit('SELECT complaint_no FROM "customer_complaint"', MFG)  # 实际：放行；期望：SQLAuditError
SQLAuditor.audit("SELECT c.complaint_no FROM inventory i, customer_complaint c",
                 {"dept": "warehouse", "roles": ["warehouse"]})          # 实际：放行；期望：SQLAuditError
```

端到端证据（实测输出）：反引号/双引号/逗号 join 三条均通过审核并经 `execute_readonly_sql` **真实返回 `customer_complaint` 数据行**（如 `{'complaint_no': 'CS-20261012-01'}`）。

根因：`text2sql.py:53-54` `_TABLE_RE = r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)"` 漏提三类表名：①括号/换行紧贴 `FROM`；②反引号/双引号包裹的标识符；③逗号隐式 join 的首表之后的表。
修复方向：审核前规范化 SQL（剥离注释、统一空白、识别引号标识符），表提取覆盖 `from\s*[(\`"]?\w+`、`, table`、`join ... on`；或引入 sqlglot 做 AST 级提取（更彻底）。

### BUG-2 复现（中危）

```python
from mfg_agents.v2.services.text2sql import FieldMasker
FieldMasker.mask([{"batch_no": "B1", "s": "千住金属"}], {"roles": ["quality"]})
# 实际：masked=False，"s" 原样输出；期望：脱敏为 "供应商***"
```

根因：`text2sql.py:180-183` 按结果列名精确匹配 `SENSITIVE_COLUMNS` 键，`SELECT supplier AS s` 后列名为 `s`，规则不触发。
影响面：quality 等可访问 `material_batch` 但不可见 supplier 的角色，经别名读取供应商原名。
修复方向：脱敏下沉到查询计划（解析 SELECT 列表的源列+别名）；轻量方案不可根治，建议 AST。

### BUG-3 复现（低危）

```python
SQLAuditor.audit("SELECT * FROM inventory", {"roles": None})
# 实际：TypeError（for r in None）；期望：SQLAuditError（fail-closed 契约一致）
```
修复：`text2sql.py:139` 改 `ctx.get("roles") or []`。

---

## C. 企微适配器密码学正确性（`tests/v2/test_qa_crypto_vectors.py`，26 用例全过）

**不信工程师自述，全部用外部锚点独立复算：**

1. **AES 原语**：FIPS-197 Appendix C.1/C.2/C.3 三种密钥长度单块向量（AES-128/192/256，工程师未测 192/256）+ NIST SP800-38A F.2.1 CBC-AES128 三块链式向量 + QA 手工逐块复核 CBC 链式语义 —— **全部与标准一致**。S-Box 数学生成正确性由向量间接锚定。
2. **企微协议层字节级独立比对**：QA 按官方文档独立拼装 `random(16)+len(4B 网络序)+msg+appid → PKCS#7@32 → AES-256-CBC(IV=key[:16]) → Base64`，与 `encrypt_msg` 输出**逐字节一致**（中文长消息跨多块同样一致）→ buffer 布局 / 补位 / IV 取法 / Base64 四要素全部正确。解密后明文结构逐字段核对（random/len/msg/appid/补位）无误。43 位 EncodingAESKey → 32 字节密钥正确。
3. **攻击面**：逐 bit 全位置翻转（密文每一字节翻转 0x80）**全部拒绝**；伪造签名（错 token/timestamp/nonce）全部拒绝；跨应用 appid 注入拒绝；msg_len 字段撒谎拒绝；适配器层 receive 双重拒绝（验签不过不进解密）。
4. **记录在案的观察项（非阻断）**：
   - OBS-1：**无重放防护**——同一合法签名重复提交被再次接受（企微官方被动回调同样依赖 5s 窗口）。生产接入需在 API 层补 timestamp 窗验重 + nonce 缓存。
   - OBS-2：截断密文（非 16 倍数）抛原生 `ValueError` 而非 `WeComCryptoError`，契约不一致（调用方只捕获后者会漏）。
   - OBS-3：`decrypt_msg` 的 `len(msg) != msg_len` 分支实际不可达（appid 校验先触发）——死代码，无害。

**结论：密码学实现与标准一致，可进入真实企微联调（联调前落实 OBS-1）。**

---

## D. HITL 与工作流真实性（`tests/v2/test_qa_hitl_workflow.py`，25 用例全过 + BUG-4 证据）

- **无自动放行路径（源码级 + 行为级双重确认）**：全 V2 源码 grep `approve/reject/advance` 仅 2 处——`node_registry.py:228` 影响工单 `advance("start")` 与引擎自身定义；**无任何跳过确认的逻辑**。行为级哨兵测试：monkeypatch 三个方法记录调用，跑完整链路，`approve/reject` **零调用**，`advance` 仅 `"start"` 一次。
- **两个 HITL 节点**：`hitl_restart`（制造现场负责人）/ `hitl_eta`（业务经理）均 `waiting_hitl`，`platform_workflow_task` 表**真实落库**（原生 sqlite3 直读验证：status/assignees/hitl_prompt/history 五字段俱全），多次运行旧待办不被消费。
- **状态机对抗**：8 组非法迁移（pending→done 跳审批、done/closed 复活、escalated→waiting_hitl、重复等待等）全部抛 `WorkflowTransitionError`；合法主路径 pending→running→waiting_hitl→running→done→closed 全通；action 白名单与状态机白名单双闸有效。
- **SLA**：`sla_hours=-1` 立即超时 → `check_sla` True → running/waiting_hitl 两状态均可 escalate；终态 SLA 停计；影响工单 P1 + 2h 窗口 + 三受理角色落库。
- **未停线分支**：B型号/DIP-2 → `停线确认=False`、HITL/工单为空、三域取证零执行（provenance 无 quality/equip/supply 查询）、报告走"运行正常"模板——**等价 V1 行为成立**。
- **BUG-4（低危）**：`WorkflowEngine("sqlite:///:memory:")` 构造后 `create_task` 报 `no such table: platform_workflow_task`。根因：`ensure_platform_schema` 走 SQLAlchemy（`:memory:` 每连接独立库），引擎 DML 走原生 `sqlite3.connect(":memory:")`，两边不是同一个库。文件库不受影响（工程师既有测试因此全部用文件库而未暴露）。修复：DDL 也改原生 sqlite3，或文档声明仅支持文件库。证据测试 `TestMemoryDbUrlBroken` 已锚定当前行为。

---

## E. Demo 三件套实测（全部 exit 0）

| 项 | 命令/方式 | 退出码 | 关键输出 | 判定 |
|---|---|---|---|---|
| CLI | `uv run python -m mfg_agents.v2.demo.cli "A型号在SMT-1停线，何时复线？"` | 0 | L0 路由命中 line_stop_v2（confidence=1.0）；三域并行取证；FA 评级B模板化降级；工单 P1/SLA 2h；2× waiting_hitl；对客报告脱敏话术；provenance 带 SQL 原文；**supply_chain 脱敏=True** | ✅ degraded 合理（LLM 不可用标记完整） |
| FastAPI | TestClient 实测 20 项断言 | 0 | `POST /api/v2/query` code=0、路由/工单/HITL/脱敏全部正确；`GET /api/v2/events` drain 21 事件（**tool 级事件缺失 → BUG-5**）；未知 request_id 404；企微 GET 校验回显明文、伪造签名 403；POST 解密→编排→加密回包，回包签名自验证通过、解密含"待人工确认"；伪造签名 403 | ✅ 功能全通，事件流有 BUG-5 |
| IM 模拟器 | `uv run python -m mfg_agents.v2.demo.im_simulator` | 0 | 真实格式回调构造→验签→解密→InboundMessage；篡改签名 False、篡改密文拒绝；全链路 33 事件（callback 路径完整）；卡片含 4 个 HITL 按钮；回包 1567 字符签名自验证 True；push 预留通道记录 | ✅ degraded 合理 |

### BUG-5 复现（中危）

```python
from mfg_agents.v2.core.events import bind_queue, unbind, QueueSink
q, token = bind_queue()
try:
    orchestrator.handle("A型号在SMT-1停线，何时复线？", ctx, sink=QueueSink())
finally:
    unbind(token)
# 实际：q.qsize() == 21（agent_start×10, agent_end×10, fan_in×1）
# 期望：33（另有 tool_start×6, tool_end×6）
```

根因：`yaml_dag.py:253-258` 为超时控制把 handler 提交到 `ThreadPoolExecutor`，worker 线程持有**全新 Context**，`events.py:42-46` `push()` 查 `_current_queue` 得到 None → 事件静默丢弃。agent 级事件在图线程发射故不受影响；CLI 走 callback 直打故不受影响——**仅 API/SSE 路径丢失全部 tool 级事件**（前端看不到三域并行取证的 SQL/RAG 过程，"事件流与 V1 前端兼容"的验收项实际不达标）。
修复方向（任选）：① QueueSink 直接持有队列引用不经 contextvar；② `pool.submit` 前 `contextvars.copy_context()` 并用 `ctx.run` 提交；③ handler 显式接收 queue。

---

## F. 工程师遗留项裁判

| # | 遗留项 | 裁判 | 依据 |
|---|--------|------|------|
| 1 | pytest dev 依赖修复 | **接受** | `pyproject.toml` 已加 `[dependency-groups] dev = ["pytest>=9.1.1"]`；`uv run pytest tests/ -q` 正常收集运行 245 用例 |
| 2 | V1 演示脚本 collect_ignore 处理 | **接受** | QA 独立验证 5 文件均为演示脚本（顶层代码/无测试函数/依赖 Ollama 或运行中服务，离线实测 502）；等价覆盖四项真实；QA 已补 events/stream 缺口（LLM 门控） |
| 3 | OTD 演示口径（shipment.eta 当要求日） | **接受（附条件）** | `otd.yaml` desc 已明示演示口径与升级路径，未伪装成真实指标；**条件：对外/上线前必须替换为真实要求日字段**，并在指标库升版 |
| 4 | 供应链 supplier 脱敏是有意加固 | **接受** | fail-closed 姿态正确且 demo 验证 masked=True；但 **BUG-2 的别名绕过削弱了该加固**——修完 BUG-2 此项决策才真正闭合 |

---

## 已知问题（不阻断，记录在案）

1. OBS-1 企微回调无重放防护（生产需 timestamp 窗 + nonce 缓存）
2. OBS-2 截断密文抛 `ValueError` 而非 `WeComCryptoError`（契约不一致）
3. OBS-3 `decrypt_msg` 长度校验分支不可达（死代码，无害）
4. OBS-4 demo 运行会把工单写入 `data/mfg_agent.db`（`WORKFLOW_DB` 默认=`DB_URL`），生产应分离库
5. OBS-5 `load_extension` 未进关键词黑名单（Python sqlite3 默认禁用兜底，建议纵深）
6. OBS-6 `test_workflow.py:83` 恒真断言（cosmetic）
7. `data/chroma/*`、`data/mfg_agent.db` 有 demo 运行产生的数据变更（运行时产物，非代码）

---

## Round 2 回归口径（工程师修复后）

1. `uv run pytest tests/ -q` 全量：9 个失败用例应全绿（BUG-1×5、BUG-2×1、BUG-3×1、BUG-5×2），其余 236 不回归
2. `TestMemoryDbUrlBroken::test_memory_db_url_broken` 为 BUG-4 的**当前行为锚定**：工程师若修复 BUG-4，该测试需翻转为断言"构造即可用"（QA 会同步更新）
3. 若 Round 2 仍有失败 → 按纪律不再进入 Round 3，剩余问题以"已知问题"结项

---

## 附：QA 新增文件清单

| 文件 | 用例数 | 性质 |
|------|--------|------|
| `tests/v2/test_qa_security.py` | 74 | 红队攻击矩阵（7 失败 = BUG 证据） |
| `tests/v2/test_qa_crypto_vectors.py` | 26 | NIST/FIPS 外部向量 + 企微协议独立字节级比对（全过） |
| `tests/v2/test_qa_hitl_workflow.py` | 25 | HITL 无自动放行 + 状态机对抗 + SLA + 落库 + 未停线分支（全过，含 BUG-4 锚定） |
| `tests/v2/test_qa_events.py` | 5 | 事件流完整性（2 失败 = BUG-5 证据） |
| `tests/v2/test_qa_v1_equivalence.py` | 3 | V1 events/stream 等价补测（1 过 + 2 LLM 门控跳过） |
| `tests/v2/qa_e2e_api_check.py` | — | FastAPI 20 项断言实测脚本（非 pytest 收集） |
