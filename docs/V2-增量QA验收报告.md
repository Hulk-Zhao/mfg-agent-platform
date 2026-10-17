# V2 增量 · QA 对抗性验收报告

- 验收对象：`mfg-agent-platform` V2 增量（4 场景补齐 + 脚手架对齐 + 意图/RAG 扩展位）
- 提交范围：`e26db61`(T01) → `ed25b5e`(T02) → `66e45b0`(T03) → `53aee21`(T04) → `648f51d`(T05)
- V1 基线：`49dcd34`；V2 脚手架基线（本次增量前）：`924c5d0`
- 验收人：QA 严过关（Edward）｜环境：Windows / Python 3.11 / Ollama **离线**
- 判定依据：`docs/V2-INCREMENT-DESIGN.md`(977)+`class-diagram.mermaid`+`sequence-diagram.mermaid`+PRD
- 验收立场：**证明它真的对**（数字与库对账、断言做变异验证、回放逐条比对），而非确认文件存在

---

## 0. 结论速览

| 维度 | 结论 | 证据段 |
|---|---|---|
| A 回归红线（302/2、V1 零破坏、冻结项） | **通过** | §A |
| B 4 个新场景端到端（数字对账 / provenance / HITL） | **通过** | §B |
| C 数据红线（含断言强度变异验证） | **通过**（断言有牙） | §C |
| D 前端与离线回放独立性 | **通过**（含 1 个逻辑缺陷） | §D |
| E 扩展位"只留位不接线" | **通过**（含 1 个契约瑕疵） | §E |
| F 文档 | **有条件通过**（示例不可直接运行） | §F |
| G `@requires_llm` 残留风险 | **不阻断交付**，须在验证报告写明 | §G |

**路由判定：全部路由至 QA（已自查修正），无需回退工程师修源码。** 发现的 4 个问题均为低/中低严重度的体验与文档问题，不涉及数字错误、不涉及数据编造、不涉及 V1 破坏、不涉及 HITL 自动放行。详见 §H。

**可交付性：可交付。** 建议随交付附 §H 的 4 条"已知问题"，由产品决定是否本轮修。

---

## A. 回归红线

### A1 全量测试复跑

```
302 passed, 2 skipped in 23.85s
```
与主理人核实结论一致（302/2/0）。2 个 skipped 明细见 §G。

### A2 V1 零破坏

```
git diff 49dcd34 648f51d -- src/mfg_agents/agents src/mfg_agents/graph \
                            src/mfg_agents/api.py src/mfg_agents/events.py
（输出为空）
```

非 v2 目录的改动仅 6 个文件，全部属已批准或已声明范围：

| 文件 | 变动 | 归属 |
|---|---|---|
| `src/mfg_agents/tools/llm_factory.py` | +62/-… | 此前已批准（tier/探针扩展） |
| `src/mfg_agents/config.py` | +22 | 已批准（V2_SCENARIOS_DIR / WORKFLOW_DB） |
| `src/mfg_agents/db/platform_schema.py` | +137（新增） | T02 数据层运行时表 |
| `src/mfg_agents/db/init_db.py` | +10 | T02（建运行时表） |
| `data/init.sql` | +15 | T02（见 §C 逐行核查：**纯追加**，既有行未改） |
| `pyproject.toml` | +7 | 已批准 |

### A3 冻结项逐项核查

**（1）现有 10 个 handler**（基线 `924c5d0` 的 `build_handlers` 注册表：`business / manufacturing / quality / supply_chain / equipment / fa / impact_assessment / hitl_restart / hitl_eta / report`）

以 AST（`ast.dump`，忽略行号）逐函数对比基线 vs 现状：

| handler | 结论 |
|---|---|
| business / manufacturing / quality / supply_chain / equipment / impact_assessment | **AST 完全相同** |
| fa | 差异仅在 `prompt = <f-string>` → `prompt = build_fa_prompt(...)`。已把基线 f-string 原文取出 eval，与新函数在 **4 组输入**（含 `defect_type=""`、`None`、空 alarms、多行 case_text）下输出**逐字节相等** |
| report | 同上，`build_report_prompt` 在 **3 组 brief**（含空 dict）下**逐字节相等** |
| hitl_restart / hitl_eta | 工厂由 `_make_hitl_handler(kind)` → `agents/hitl.py::make_hitl_handler(engine, notifier, kind)`，函数体逐块等价（唯一差异：`ctx` 注解 `SharedContext` → `Any`） |

> 附注：新增的 `equipment_warn` 是**包装器**（调用现有 `equipment` 后补 `has_alarm` 条件键），未修改 `equipment` 本体。

**（2）`scenarios/line_stop_v2.yaml`**：在 `e26db61^..648f51d` 的变更清单中**不存在**该文件 → 未改。

**（3）`FORBIDDEN_WORDS` 与 3 个模板函数**（迁移至 `prompts/report.py`）AST 对比基线：

```
✅ FORBIDDEN_WORDS            逐字节同 AST
✅ _fa_template               逐字节同 AST
✅ _apply_forbidden_words     逐字节同 AST
✅ _customer_report_template  仅 ctx 注解 SharedContext→Any，函数体同 AST
```
`node_registry` 仍以模块符号再导出（`node_registry._fa_template is prompts_report._fa_template` 由金标准测试守护）。

**（4）`services/text2sql.py` → 包化**（对外符号与行为）

以 AST 逐个对比基线 `text2sql.py` 与包内 4 个模块：

```
✅ TABLE_ACL / SENSITIVE_COLUMNS / _FORBIDDEN_SQL / _parse_sql / extract_tables
✅ QueryResult / SQLAuditError / SQLAuditor / ResultValidator / FieldMasker
✅ MetricLibrary / QueryLibrary
⚠ Text2SQLService：7 个方法中 6 个 AST 完全相同；query_detail 唯一差异 =
   prompt 组装改走 build_detail_prompt(question)，展开即
   f"{SCHEMA_PROMPT}\n\n问题：{question}"（与原实现字面一致，SCHEMA_PROMPT 仍为调用时惰性导入）
```
`__all__` 由 10 项 → 11 项（新增 `extract_tables`，基线下**本就可 `from ... import extract_tables`**，属兼容超集）。11 个符号全部 `from mfg_agents.v2.services.text2sql import X` 成功。

### A4 `test_router.py` 唯一获批断言翻转

`e26db61^..648f51d` 期间 `tests/` 的全部变更只有 3 处：

```
A  tests/v2/test_prompts_golden.py
A  tests/v2/test_scenarios_v2.py
M  tests/v2/test_router.py      ← 唯一被修改的既有测试
```

该修改的完整 diff（`648f51d^..648f51d`）只有 1 条断言翻转 + 函数名/docstring 说明：

```diff
-    """L0：V1 SCENE_RULES 关键词命中（未迁移场景 → domain 有、scenario None）。"""
+    """L0：V1 SCENE_RULES 关键词命中（S3 已迁移 → scenario=customer_complaint_v2）。
+    说明（§8.3 唯一预期内断言翻转）...
-    assert result.scenario_key is None
+    assert result.scenario_key == "customer_complaint_v2"
```
`level` / `domain` 断言未动。**未发现其他测试被软化**（无 `pytest.skip` 新增、无 `xfail`、无断言删除、无 `-k` 排除）。

---

## B. 4 个新场景端到端（真实运行取证）

自起真实服务：`.venv/Scripts/python.exe -m uvicorn mfg_agents.v2.demo.api:app --port 8006`，
用 `curl POST /api/v2/query` 独立跑 5 个输入（Ollama 离线 → degraded 模式），逐项与 SQLite 库对账。

### B1 S2 来料异常 `incoming_quality_v2`

- 输入：`今天锡膏批次有问题`（route.level=L0, confidence=1.0）
- **节点序列（真实 timings）**：`intake → sqe_assess → {planning ‖ procurement ‖ material_control} → scar_wf → hitl_disposition → report_incoming`
  （`fanin` 为结构节点不计时；与设计文档序列一致）
- **数字对账**：
  | 报告中的数 | 库内真值 | 结论 |
  |---|---|---|
  | 2 个不合格批次 | `material_batch WHERE incoming_result='fail'` = 2 行（MAT-SP-1014 @10-14、MAT-IC-1011 @10-11） | ✅ |
  | 排产影响 计划10000/实际6800 | `production_output WHERE model='A型号'` = 5000+5000 / 5000+1800 | ✅ |
  | 可用库存 2 个型号仓、0 个为 0 | `inventory` 2 行，A=1200-800=400、B=5000-500=4500 | ✅ |
  | 供应商跟催 2批/不合格1（SP-001、IC-100） | `material_batch GROUP BY supplier,part_no` | ✅ |
- **provenance 完整性**：含 `iqc_batches_recent` 的**真实 SQL 原文** + `row_count=2`；`masked=true`，摘要渲染为 `供应商 供应商***`、`COA http://coa.example.com/sp1014.pdf`（角色 `coordinator` 下供应商按字段级脱敏）✅
- **HITL**：`hitl_disposition` = `waiting_hitl`，审批人 `SQE/PQE/生产`，4 个动作（退货/换货/特采/挑选）；`scar_flow` 工单 `running` + P1 通知 3 角色 ✅
- degraded=true（`报告生成 LLM 不可用：模板化 + 禁词表兜底`）

### B2 S3 客户投诉 8D `customer_complaint_v2`

- 输入：`华为投诉BATCH-A-1008`
- 节点序列：`intake → qa_lead → {pqe_process ‖ sqe_supplier ‖ engineering ‖ business_sales} → capa_flow → hitl_d8_close → hitl_complaint_reply → report_complaint`
- **8D 骨架 D1–D8 齐全** ✅；**D2 = 华为终端**（`customer_complaint.customer` 真值）✅
- **不良数与库一致**：D4/PQE = `贴片偏移 46pcs`，库内 `quality_defect(batch_no='BATCH-A-1008')` = `贴片偏移 46 / total 2000` → 46/2000=2.3%，与客诉单 `defect_desc` "不良率约2.3%" **自洽** ✅
- **工程根因不编造**：`设计根因：无 BOM/ECN/图纸数据源，未编造结论`，provenance 有 `channel=data_gap`，简报含 `数据缺口` 键 ✅
- **双 HITL**：`hitl_d8_close`（质量总监/QA经理）+ `hitl_complaint_reply`（业务经理），**均 waiting_hitl** ✅
- 对客报告为 8D 模板且经禁词表兜底（无"停线/根因/维修"等）✅

### B3 S4 出货风险 `delivery_status_v2`

- 输入：`A型号能发多少货`
- 节点序列：`intake → {business_order ‖ planning ‖ material_control ‖ warehouse_atp ‖ manufacturing_output ‖ logistics} → atp_analysis → delivery_risk_wf → hitl_delivery_commit → report_delivery`（**六域并行** ✅）
- **ATP 对账**：`ATP=400pcs` = `inventory.on_hand 1200 - reserved 800` ✅；`订单需求 3500 = 2000(SO-…15-01) + 1500(SO-…10-03)` ✅；`缺口 3100 = 3500-400` ✅；三者前后一致
- **provenance 完整性（重点核查 `_collect_provenance` 改动）**：注册查询 **8 条**，六域 SQL 全部在册：
  `orders_by_model`, `plan_impact_by_material`, `inventory_available_all`, `shortage_by_model`, `warehouse_available`, `output_by_model`, `shipment_status`×2，外加 `channel=compute` 条目（`formula="on_hand - reserved"`, `available_qty=400`, `gap_qty=3100`）。
  覆盖 3 张表（`inventory` / `shipment` / `production_output`）。
  > 对主理人任务书"四表"的更正：**业务上只需 3 表**，六节点的 SQL 一条不缺；"5 个 supply_chain 分支收敛成 1 条"的旧缺陷确认已修复。
- **稳定性复跑 3 次**：注册查询**集合恒为 8 条**（不丢）；顺序随并行完成次序变化（并行特性，非缺陷；前端按节点渲染，无顺序依赖）
- HITL `hitl_delivery_commit` waiting_hitl（业务经理）✅

### B4 S8 设备预警 `equipment_warning_v2`

- 输入：`SMT-1设备参数异常吗`
- 节点序列：`intake → equipment_check → {maintenance ‖ planning_window ‖ engineering_param ‖ spare_parts} → maintenance_flow → hitl_maintenance_window → report_equipment`
- **告警 3 条真实取证**：简报逐条列出 `PRT-01 刮刀压力=0.8MPa（基准0.5）` / `PRT-01 钢网清洁周期=15min（基准4h）` / `OVN-02 回流温区3=245℃（基准235±5）`，与 `equipment_log WHERE line_code='SMT-1' AND is_abnormal=1` 的 3 行**逐字段一致** ✅
- **参数/备件 data_gap**：`工艺参数建议：数据缺口（无参数基线数据源）`、`备件需求：数据缺口（无备件数据源）`，provenance 4 条 `data_gap`（含 note）✅
- **HITL** `hitl_maintenance_window` = waiting_hitl，审批人 `制造现场负责人` ✅

### B5 五场景共性

- 5 个场景 `degraded=true` 且 `degraded_reasons` 均给出可审计原因；
- `report`（对客文本）仅 S3/S4 非空 —— 与各 YAML `outputs` 声明一致（S2/S8 无对客报告），**不是缺陷**；
- line_stop 复跑见 §C3。

---

## C. 数据红线（含断言强度变异验证）

### C1 红线事实核查

| 检查 | 结果 |
|---|---|
| `material_batch` 是否存在 `date(arrival_time)='2026-10-15' AND incoming_result='fail'` | **0 行** ✅（fail 仅 MAT-SP-1014@10-14、MAT-IC-1011@10-11） |
| `supply_batches_today`（today=2026-10-15）行数与 rows[0] | **1 行**，`rows[0].supplier=千住金属`，`incoming_result=pass` ✅（与基线一致） |
| `inventory` 行数 | **2 行** ✅（`test_text2sql` 明细探查 COUNT 断言依赖） |
| `data/init.sql` 改动性质 | **纯追加**：仅新增 quality_defect#5、material_batch#4-6、shipment#3；既有行 id/值未动 ✅ |

### C2 断言强度验证（变异测试，已还原）

「加了断言锁死」不能只看代码存在，故做**破坏性变异**：备份 DB → 注入 `material_batch('MAT-QA-MUT-9', fail, 2026-10-15 20:00)` + `inventory('QA-MUT型号')` → 跑测试 → 回滚。

```
注入后：material_batch 10-15&fail = 1 行，inventory = 3 行
FAILED tests/v2/test_scenarios_v2.py::test_s2_incoming_quality_end_to_end            （iqc row_count==2 被打破）
FAILED tests/v2/test_scenarios_v2.py::test_data_redline_material_batch_avoids_10_15
FAILED tests/v2/test_scenarios_v2.py::test_data_redline_supply_batches_today_unchanged （2 == 1）
FAILED tests/v2/test_scenarios_v2.py::test_data_redline_inventory_count_unchanged      （3 == 2）
4 failed, 8 passed
回滚后：12 passed，DB md5 与注入前一致
```
**结论：3 条数据红线断言 + S2 场景断言均有真实约束力**，不是摆设。

> 小瑕疵（不阻断）：`tests/v2/test_scenarios_v2.py` 的 `DB_PATH = "data/mfg_agent.db"` 是相对路径，只有在仓库根目录执行 pytest 才成立；从其他 cwd 跑会 `sqlite3.OperationalError`。

### C3 line_stop 数据未被新数据破坏（实测复跑）

```
供应链 = "今日1个批次到货，无来料异常"          ✅ 断言未被破坏
对客报告 = "…目前产线处于计划内暂停（例行校准）…"  ✅ 禁词"停线"已替换，无 6 个禁词
品质     = "主导不良：锡膏偏移，共77pcs"          ✅ = 32+45（新增 BATCH-A-1008 行是 SMT-2，
                                                被 quality_defect_dist 的 line_code 过滤，红线守住）
FA评级   = B（77<100 规则）                       ✅
```
`data/init.sql` 新增的 `(5,'BATCH-A-1008','A型号','SMT-2','贴片偏移',46,2000,…)` 设计上正确规避了 SMT-1/A型号 聚合口径。

---

## D. 前端与离线回放

### D1 真实链路 5 chip 跑通

`curl` 逐个打 5 个 chip 明文（与 `QUICK_QUESTIONS` 完全一致）：

| chip | HTTP | 场景命中 |
|---|---|---|
| A型号在SMT-1停线，何时复线？ | 200 (0.68s) | line_stop_v2 |
| 今天锡膏批次有问题 | 200 (2.51s) | incoming_quality_v2 |
| 华为投诉BATCH-A-1008 | 200 (1.75s) | customer_complaint_v2 |
| A型号能发多少货 | 200 (0.19s) | delivery_status_v2 |
| SMT-1设备参数异常吗 | 200 (0.15s) | equipment_warning_v2 |

静态资源：`/demo-ui` → 307 → `/demo-ui/` 200(3994B)；`app.js` 200(22417B)、`screens.js` 200(65743B)、`demo-data.js` 200(108144B)、`app.css` 200(38249B)；`node --check` 三个 JS **语法全部 OK**。

> 首请求 2.5s / 1.75s 属 `llm_available()` 首次探针 1.5s 超时（后续 30s TTL 缓存），非缺陷，但值得写进"生产注意"。

### D2 `data_gap` 是否真实渲染

- 前端逻辑：`screens.js:906` 过滤 `provenance.channel === "data_gap"` → `:940` 渲染醒目横幅；`:1005` 在 provenance 列表逐条渲染 `数据缺口（无来源，不编造）：{note}`。
- 真实输出喂入验证：S3 有 2 条、S8 有 4 条 `{channel:"data_gap", domain, note}`，**字段名 `note` 与前端读取完全一致**；`gapNotes` 经 `new Set()` 去重后再拼接，UI 不会出现重复文案。
- 结论：**S3 / S8 的 data_gap 会真实渲染** ✅

> 后端侧小问题：`report.py` 会在自己的 provenance 里**重复 emit 一次 data_gap**（S3 2 条→实际同一 note 出现 2 次，S8 4 条→2 个 note 各 2 次），来源 domain 还被覆盖成 `report`。UI 横幅已去重不受影响，但 provenance 列表会重复显示两行。见 §H-1。

### D3 离线回放独立性（抽查 2 场景，逐条比对）

方法：真实 SSE 采集（`POST /api/v2/events/open` 预开队列 → `GET /api/v2/events` 订阅 → `POST /api/v2/query`）→ 与 `demo-data.js` 的 `CHAT_DEMOS[key].events` 做**多重集逐字段比对**（忽略 `elapsed_ms / run_id / task_uid / ts / icon` 等易变量）。

| 场景 | demo 事件 | 真实事件 | 节点集 | 逐条字段差异 |
|---|---|---|---|---|
| `A型号在SMT-1停线，何时复线？` | 33 | 33 | 11 个，**完全一致** | **仅 1 条**：工单号 `wf-7a97b342ab44` vs `wf-6b70aa778036`（随机 uid） |
| `华为投诉BATCH-A-1008` | 37 | 37 | 11 个，**完全一致** | **仅 2 条**：同属工单号 `wf-c4bd412f60e1` vs `wf-df2209fdb84e` |

即：事件条数、事件类型序列（顺序无关）、节点名、label、status、summary 里的全部业务数字（77pcs / 3 条告警 / 46pcs / ATP 400/3500/3100 / 供应商*** 等）**逐字节一致**，唯一差异是每次新建工单的随机 uid。
**结论：`demo-data.js` 的 `CHAT_DEMOS` 确由真实运行采集生成，非手写编造** ✅（demo 事件额外裁掉了 `icon` 字段，前端用 `NODE_LABELS` 兜底，属采集后加工，可接受）

**未收录问题提示 —— 发现缺陷**：
`screens.js:729` `const demo = demos[text] || window.DEMO.CHAT_DEMO;` 中 `CHAT_DEMO` 是 line_stop 的整份回放兜底，导致 `:730` 的"该问题未收录离线回放…请启用在线模式（起后端服务）"分支**永远不可达**。离线态下，用户输入任意非 5 个 chip 的问题，界面会**静默播放"停线"场景的回放**（含"是"标记的演示徽标，但问题与回放内容不匹配）。见 §H-2。

### D4 常量覆盖度

| 常量 | 数量 | 覆盖 5 场景 |
|---|---|---|
| `QUICK_QUESTIONS` | 5 | ✅ 与 5 个场景 key 一一对应（已实测逐个命中） |
| `NODE_LABELS` | 43 | ✅ 5 场景全部节点有中文名；`intake` 未定义但事件自带 `label`，渲染兜底生效 |
| `PARALLEL_NODES` | 18 | ✅ 覆盖 S1(3) + S2(3) + S3(4) + S4(4) + S8(4) 并行分支 |

---

## E. 扩展位"只留位不接线"核验

### E1 `contracts/vector_store.py`

- ABC 四方法 `upsert / query / delete / health`，`VectorHit` dataclass 带 `id/text/metadata/score`，签名合理；
- `ChromaVectorStore` 惰性取 collection（转发既有 `tools/rag_tools._get_collection`），默认 collection 恒为 `failure_cases`，`query` 语义与 `rag_tools.search_cases` 一致 → **行为不变**（现有 RAG 相关测试全绿，302 passed 为证）；
- **零新依赖确认**：`pyproject.toml` 与 `uv.lock` 均无 `pymilvus / milvus / faiss / sentence-transformers / redis`；全仓 `grep` 命中的 2 处均为**注释里的禁用说明**。

> 契约瑕疵（低）：ABC 的 `upsert/query/delete` 都收 `collection: str` 参数，但 `ChromaVectorStore` 内部恒用 `__init__` 传入的单一 collection，**参数被静默忽略**；`health()` 也只返回单 collection 的 count。未来接 Milvus 多 collection 时该签名会自相矛盾。建议 ABC 去掉 `collection` 参数，或适配器改为按参数取 collection（并可缓存多个）。

### E2 `intent/` 未接线

- `core/router.py` 的 import 只有 `tools.llm_factory / contracts.scenario / prompts.routing` —— **未 import intent**；
- 全仓 `grep "v2.intent|from ..intent|import intent"` → **0 命中**（仅 `intent/__init__.py` 内部自引）；
- 骨架内容仅为：`IntentResult`（与 `RouteResult` 字段一一对应）、`IntentClassifier`(ABC)、`RuleLLMClassifier`（内部包装现 `TwoLevelRouter`，行为等价）、`Strategy`/`StrategySelector`(ABC)/`DefaultStrategySelector`，加迁移路径注释 ✅

### E3 `prompts/` 金标准测试能否守住不变量（变异验证）

```
变异：prompts/fa.py 首句 "专家" 后追加 1 个全角句号
→ FAILED tests/v2/test_prompts_golden.py::test_build_fa_prompt_bytes  (1 failed, 9 passed)
还原 → 10 passed（git diff 为空）
```
**金标准测试真有牙** ✅。`node_registry` 的 prompt 来源确已切换到 `prompts.fa.build_fa_prompt` / `prompts.report.build_report_prompt`，行为不变由 §A3 逐字节等价 + line_stop 回归 + 302 全绿共同证明。

### E4 `_collect_provenance` 改动在 line_stop 下的等价性

方法：`git worktree` 检出基线 `924c5d0`（旧 `_collect_provenance(dag)`）与当前 `648f51d`（新 `ctx.history("provenance")`），**同一输入**跑 line_stop，规范化后对比（剔除 `elapsed_ms` / `run_id` / `task_uid`）。

| 字段 | 结论 |
|---|---|
| `scenario_key` / `degraded` / `degraded_reasons` / 节点集合 / `route` / `internal_brief` / `hitl` / `report` | **完全一致** |
| `provenance` 条数 | 旧 19 → 新 20 |
| `provenance` 内容多重集 | 差异**仅 1 条**：新增 `{"channel":"hitl","approver_roles":["制造现场负责人"]}` |
| 差异性质 | 旧实现按 `dag.domains` 收敛，两个 HITL 节点同属 `hitl` 域 → **last-write-wins 丢掉了 `hitl_restart` 的 provenance**；新实现找回。**这是修复，不是回归** |

> 可观察行为变化（记录备案）：provenance **顺序**由"域名字典序"变为"执行顺序"，并行分支的先后不固定（S4 三次复跑已验证：集合稳定、顺序变化）。当前无测试/前端依赖该顺序，但若未来有人按索引取值会脆。

---

## F. 文档

### F1 `docs/V2-接口文档.md`

- 端点覆盖：文档列 9 个，实际 `app.routes` 9 个业务端点，**完全对齐**（含 `GET+POST /api/v2/im/wecom/callback`、`/demo-ui/*`）。
- 抽查 2 个端点与真实返回对照：
  - **4.2 `POST /api/v2/query`**：数据字段表逐项与真实响应一致（`query/run_id/route/scenario_key/report/internal_brief/provenance/hitl/workflow_tasks/timings/degraded/degraded_reasons/fallback/elapsed_ms/error` + 回显 `request_id`）；示例中 `ATP=400/3500/3100`、`hitl kind=delivery_commit`、`degraded_reasons` 与实测**逐字一致** ✅
  - **4.5 `GET /api/v2/workflow/pending`**：实测 13 条待办，字段集 `[assignees, context, created_at, history, hitl_prompt, priority, sla_due_at, status, task_uid, title, updated_at, wf_id]` 与文档示例一致；`history` 首条实测为 `create(pending→waiting_hitl)`，与文档示例**完全一致** ✅
  - 附带验证状态机：`approve`（waiting_hitl→running，`final=true`→done）返回 200；对 `running` 工单 `reject` → `closed` 200（`TRANSITIONS` 允许 `running→closed`）；不存在工单 → 404 `工单不存在`；与文档错误码（404/409）一致。
- 建议补一句"未配置 IM 环境变量时 wecom 端点返回 500"已在 4.8 写明 ✅

### F2 `v2/README.md` 接入指南可执行性

| 指南 | 静态/动态核对 | 结论 |
|---|---|---|
| 一、接入新 IM | `IMGateway` 契约方法（`verify_callback/receive/reply/push`）与实现一致；`WorkflowEngine.advance(task_uid, action)` 存在 | 可执行 ✅ |
| 二、接入新数据源 | `DataSourceRegistry.register(source, default=False)` 签名与示例一致；`TABLE_ACL` fail-closed 真实存在 | 可执行 ✅ |
| 三、新增场景（YAML，**"零代码"**） | 示例 YAML 可被 `yaml.safe_load` 解析；引用 agent = `material_control / planning / procurement / hitl_urgent_po`，其中 **`hitl_urgent_po` 未注册** | **示例不可直接运行** ⚠ |

实测证据：`yaml_dag.py:242-244` 对未注册 handler 直接 `raise KeyError("节点 'urgent_po' 未注册 handler: 'hitl_urgent_po'")`。
即"零代码"只对**复用已有 handler** 成立；新增 `hitl` kind 必须注册一次（README 下文有"新 Agent handler 注册一次即可被所有场景引用"，但"零代码"的措辞与示例相矛盾）。见 §H-3。

> 正面结论：**5 个真实场景的全部 agent 引用均已注册**（refs 7/8/9/9/10，未注册集合为空），框架装配无缺口。

---

## G. `@requires_llm` 残留风险（专项）

**这 2 个 skipped 测试是**（`pytest -rs` 明细）：
```
SKIPPED tests/v2/test_qa_v1_equivalence.py:26  V1 图执行需真实 LLM（Ollama），离线跳过
SKIPPED tests/v2/test_qa_v1_equivalence.py:39  V1 图执行需真实 LLM（Ollama），离线跳过
```

**它们断言什么**
1. `test_v1_event_queue_push_during_graph_run`：`app.invoke({"user_query": "A型号在SMT-1停线了，什么时候能复线？"})` 后 → `scenario == "line_stop"`、`customer_report` 非空、`events.get_queue().qsize() > 0`（清空存量后）。
2. `test_v1_graph_stream_values_mode`：`app.stream(..., stream_mode="values")` 步数 ≥ 2、首态含 `scenario`、末态含 `customer_report`、末态字段集 ⊇ 首态。

**为何依赖 LLM**：V1 LangGraph **没有 degraded 模式**，FA 节点与报告节点必须真调 `ChatOllama` 才能产出 `customer_report`。离线时这两个测试本身不可跑 —— 这也正是 `tests/conftest.py` 用 `collect_ignore` 跳过 5 个 V1 演示脚本的正当性证据。

**在真实 Ollama 下失败的可能原因（按概率排序）**
1. **模型名/已拉取状态不匹配（主因）**：`requires_llm` 用的是 `llm_available()`，而该探针只 `GET {base}/api/tags` 判 `status==200`，**不校验** `settings.OLLAMA_MODEL="qwen3.5:9b"` / `OLLAMA_MODEL_L1="qwen3.5:4b"` 是否真的存在。Ollama 起了但模型未 pull（或 tag 不同，如 `qwen2.5:7b`）时 → skip **不生效**、测试真跑 → `ChatOllama` 调用抛 model not found → **FAILED**。这也解释了工程师"沙箱 Ollama 可达时这 2 个测试会失败"的自述。
2. **超时**：9B 模型本地推理跑完一整条 V1 图（FA + 报告两次生成），可能超出 CI 惯例时限，表现为慢/中断。
3. **输出格式**：FA 节点对 LLM 输出做 JSON 正则解析，格式不合规会走异常分支，但这两个测试只断言 `customer_report` 非空，风险较小。
4. **skip 条件求值时机**：`requires_llm = pytest.mark.skipif(not llm_available(), ...)` 在 **collection 期**定值，且探针有 30s TTL 缓存，fixture 里的 `reset_probe_cache()` 对它无效 → 同一次会话内"环境变了"不会被感知。

**结论**：属 **"环境假设问题"为主 + 测试设计瑕疵**（把"端口通"当成"能推理"），不是被测代码的缺陷，**不阻断交付**。建议（三选一或组合）：
- 探针增强：读 `/api/tags` 的 `models[].name`，校验目标 model 存在才算 available（影响面小，最推荐）；
- 测试内 `try/except` 捕获模型缺失/超时后 `pytest.skip(reason=...)`；
- CI 显式声明离线（`OLLAMA_BASE_URL` 指向不可达地址 / 加 `LLM_PROVIDER=none`），让 skip 稳定生效。

---

## H. 发现的问题清单（均不涉及正确性红线）

| # | 严重度 | 位置 | 现象 | 复现 | 建议 |
|---|---|---|---|---|---|
| H-1 | 低 | `v2/agents/report.py:144-146` + `orchestrator._collect_provenance` | `data_gap` provenance 重复：来源节点 emit 一次，报告节点再重复 emit 一次（并覆盖 `domain` 为 `report`）。S3 实测 2 条同 note，S8 实测 4 条=2 note×2 | `curl POST /api/v2/query -d '{"query":"华为投诉BATCH-A-1008"}'` → `provenance` 中 `channel=data_gap` 计数=2，`note` 完全相同 | 报告节点不再重复 emit（前端横幅已 `Set` 去重，但 provenance 列表会重复显示） |
| H-2 | 中低 | `v2/demo/ui/screens.js:729` | 离线演示态下，`demos[text] \|\| window.DEMO.CHAT_DEMO` 使"未收录问题"分支（:730）**永不可达**；输入任意非 5 chip 问题会**静默回放"停线"场景** | 断网/停后端 → 智能问数输入"明天天气" → 播放 SMT-1 停线回放并给出停线结论 | 去掉 `\|\| window.DEMO.CHAT_DEMO` 兜底，让"未收录…请启用在线模式"真正生效 |
| H-3 | 低 | `v2/README.md` 指南三示例 | 示例 YAML 引用未注册的 `hitl_urgent_po`，"零代码"措辞与示例矛盾，直接拷贝运行会 `KeyError: 节点 'urgent_po' 未注册 handler` | 按示例建 YAML 跑 → `yaml_dag.py:244` 抛错 | 措辞改为"复用已有 handler 则零代码；新增 hitl kind 需注册一次"，或把示例改成已有 kind |
| H-4 | 低 | `v2/contracts/vector_store.py` | ABC 的 `collection` 参数在 Chroma 适配器内被忽略（恒用单 collection），`health()` 不返回 collection | 读代码即可 | 接 Milvus 前收敛签名（去掉参数或按参数取 collection） |

补充小瑕疵（不计为问题，记录备案）：
- `tests/v2/test_scenarios_v2.py:18` `DB_PATH` 为相对路径，仅能在仓库根目录执行；
- `screens.js` 的"报告中心"页是纯 demo 数据（已 `demoFlag` 标注），S3/S4 的对客报告在**智能问数结果卡**中不展示（`resultCardHtml` 未渲染 `result.report`，仅 `fallbackCardHtml` 渲染）——信息缺失但不误导；
- 首个请求因 `llm_available()` 探针（1.5s 超时）会慢 1.5s 左右（30s TTL 缓存），生产可预热；
- S2 的 SQE 判定文案形如 `2 个不合格批次：MAT-SP-1014（…）`，只点名 `rows[0]`，读起来像 1 个（数据本身在 provenance 完整）。

---

## I. 未改动源码声明与现场还原

- **未修改任何源码**；变异均通过"备份→改→跑→还原"完成并已 `git diff` 复核为空。
- `data/mfg_agent.db`：注入的 `MAT-QA-MUT-9` / `QA-MUT型号` 已回滚；API 验收产生的运行时表（`platform_workflow_task` / `platform_metric_def`）在收尾时还原至 HEAD 版本。
- 临时 worktree（`924c5d0` / `53aee21`）已 `git worktree remove`，`git worktree list` 仅剩主工作区。
- 取证脚本与原始响应留档于 `.tmp-qa/`（`s1..s8.json`、`sse_s1.txt`、`sse_s3.txt`、`linestop_old.json`、`linestop_new.json`、`replay_diff.py`、`replay_multiset.py`、`run_line_stop.py`）。

---

## J. 路由判定

| 判定项 | 结论 |
|---|---|
| 源码 Bug（需回退工程师） | **无**。数字全部与库对账一致；无编造；HITL 全部 `waiting_hitl` 未自动放行；V1 零破坏 |
| 测试自身 Bug（QA 自查修正） | **无**。302/2 复现稳定；变异验证证明数据红线断言有效；未发现需要修正的测试断言 |
| 已知问题（文档记录，不阻断） | §H 的 4 条（H-2 建议本轮修，其余可排后续） |

**验收结论：V2 增量通过 QA 验收，可交付。**

---
---

# Round 2 章节 · 修复后最终回归（commit `3f95721` → `b1ec49d`）

- 验收范围：**delta + 防软化**（Round 1 的对抗矩阵结论继续有效，不重复）
- 基线：`648f51d`（Round 1 验收对象）→ `3f95721`（修复 H-1/H-2/H-3 + G 探针）→ `b1ec49d`（修复 R2-1）
- 环境：同上（Ollama 离线）

## 0. Round 2 结论速览

| 项 | 结论 |
|---|---|
| A 回归与防软化 | **通过**（R2-1 已在 `b1ec49d` 修复并复验，见 §I） |
| B H-2 离线行为 | **通过**（独立复验，0 失败断言） |
| C H-1 data_gap 事实核验 | **通过**（S3=1 / S8=2，主理人裁定成立） |
| D H-3 README 示例 | **通过**（原样拷贝实际跑通，无 KeyError） |
| E G 探针 + H-4 | **通过**（变异验证探针断言有牙） |
| I R2-1 复验 | **通过**（14/14 异常矩阵 + 6/6 集成级，单文件 `1 passed, 2 skipped`） |

**最终测试数字：308 passed / 2 skipped / 0 failed（两次独立复跑一致）；`b1ec49d` 单文件复验 `1 passed, 2 skipped`。可交付。**

---

## A. 回归与防软化

### A1 全量测试 ×2

```
（--basetemp 落到工作区，规避沙箱 safe-delete 钩子截断汇总行）
run1: exit=0  308 passed, 2 skipped
run2: exit=0  308 passed, 2 skipped
```
`pytest --collect-only` = **310 tests** = Round 1 的 304 + 新增 `test_llm_probe.py` 6 条。与主理人本地结果一致。

> 环境噪音记录：不带 `--basetemp` 时，pytest 清理 `%TEMP%\pytest-of-*\garbage-*`（123 文件）会触发沙箱 `[safe-delete]` 钩子，**截断汇总行并把 exit code 置 1**，但日志中 `310 个点、0 个 F/E` 全部通过。这属本机沙箱行为，不是测试失败（改用 `--basetemp=.tmp-qa/ptN` 即恢复正常退出码）。若主理人复跑看到 exit=1 而全是点，可按此解释。

### A2 改动范围核对（`git diff 648f51d 3f95721 --stat`）

```
 docs/V2-增量交付-验证报告.md        |  73 ++   （工程师自述）
 src/mfg_agents/tools/llm_factory.py |  69 ++   （G 探针模型校验）
 src/mfg_agents/v2/README.md         |  36 ++   （H-3 + H-4 已知限制）
 src/mfg_agents/v2/agents/report.py  |  23 ++   （H-1 data_gap 去重）
 src/mfg_agents/v2/demo/ui/screens.js|   5 +-   （H-2 兜底删除）
 tests/v2/test_llm_probe.py          |  87 ++   （A 新增）
 tests/v2/test_qa_v1_equivalence.py  |  26 +-   （A 防环境误报）
 tests/v2/test_scenarios_v2.py       |  13 +-   （C 断言加强）
```
共 8 个文件，**与主理人列出的预期清单完全一致，无多余文件**。`pyproject.toml` / `pytest.ini` / `conftest.py` 均未改（不存在通过配置静默排除测试的路径）。

**测试侧仅 3 个文件变动，且全部是"加严"而非"软化"**：

| 文件 | 变动性质 | 判定 |
|---|---|---|
| `test_scenarios_v2.py` | S3：`any(...)` → `len(gaps)==1` + `domain=="rd_engineering"` + 排除 report 域；S8：新增 `len==2` + domain 集合 + note 唯一 | **断言加强** ✅ |
| `test_qa_v1_equivalence.py` | 新增 `_skip_if_llm_not_ready`；try 只包 `app.invoke/stream`，**所有 assert 留在异常处理之外** | 主体正确，有 1 处瑕疵（R2-1） |
| `test_llm_probe.py` | 纯新增 6 条 | ✅ |

全区间新增的 `pytest.skip` 仅 1 处（即 R2-1 涉及的那行）。未发现 `xfail`、`filterwarnings`、断言删除、`-k` 排除。

### A3 `test_llm_probe.py` 断言强度（变异验证，已还原）

6 条测试**不是**"只断言函数存在"，而是真实 mock 了 `urllib.request.urlopen` 的多种响应：

| 用例 | mock 的场景 | 断言 |
|---|---|---|
| `..._both_models_pulled` | 200 + `{models:[qwen3.5:9b, qwen3.5:4b, llama3:8b]}` | `is True` |
| `..._l1_model_missing` | 200 + 仅 L2 模型 | `is False` |
| `..._l2_model_missing` | 200 + 仅 L1 模型 | `is False` |
| `..._port_unreachable` | `urlopen` 抛 `URLError` | `is False` |
| `..._http_not_200` | 500 + `{}` | `is False` |
| `..._normalizes_untagged_config_to_latest` | 配置 `qwen3.5` ↔ 已装 `qwen3.5:latest` | `is True` |

**变异验证**（把 `return required <= installed` 改成 `return bool(installed)`，即回退为"只看服务可达"）：

```
FAILED test_probe_false_when_l1_model_missing
FAILED test_probe_false_when_l2_model_missing
2 failed, 4 passed
```
还原后 `6 passed`。**结论：探针测试确实守住了"模型必须已 pull"这一新不变量** ✅
（唯一未覆盖的是 `LLM_PROVIDER=openai` 分支的 `_probe_openai`，当前部署未用 openai，记为后续可选补测。）

### A4 `@requires_llm` 防误报复核 —— **发现 R2-1（部分 AssertionError 会被吞）**

实测 `_skip_if_llm_not_ready` 在 8 种异常下的行为：

| 输入的异常 | 期望 | 实际 |
|---|---|---|
| `AssertionError("assert final.get('customer_report') 失败")` | raise | **raise** ✅ |
| `AssertionError("state 缺少 model 字段")` | raise | **skip** ❌ |
| `AssertionError("ollama 场景断言失败")` | raise | **skip** ❌ |
| `KeyError("scenario")` | raise | raise ✅ |
| `ValueError("配方错误")` | raise | raise ✅ |
| `ConnectionError("connection refused")` | skip | skip ✅ |
| `RuntimeError("model 'qwen3.5:9b' not found, try pull")` | skip | skip ✅ |
| `TimeoutError("timed out")` | skip | skip ✅ |

**成因**：`hint = ("not found", "pull", "model", "connection", "refused", "timed out", "timeout", "ollama")` 中 `"model"` / `"ollama"` 是**裸子串**，任何 message 里带这两个词的 `AssertionError` 都会被当作"LLM 不可用"跳过。

**主理人的判断需要修正一处**：`test_qa_v1_equivalence.py` 的 assert 确实都在 try 之外（这点成立，测试体内的断言失败仍会 FAILED）；但**如果 `app.invoke/app.stream` 内部**抛出 message 含 `model`/`ollama` 的 `AssertionError`，会被静默 skip。可参考的缓解事实：V1 业务代码 `grep "assert "` 在 `src/mfg_agents/{graph,agents,tools}` 下**为空**（无业务 assert），因此该路径实际由 langgraph/langchain 内部 assert 触发，概率低。

**严重度：低**（不影响离线 308/2，不影响测试体断言正确性）。
**建议修法（二选一，1 行）**：① 函数开头加 `if isinstance(exc, AssertionError): raise exc`；② 收窄 hint 为 `("model not found", "no such model", "pull model", "connection", "refused", "timed out", "timeout")`。
**裁定：不阻断交付**。工程师已在 `b1ec49d` 按方案 A 修复（守卫前置），复验见 **§I**。

---

## B. H-2 离线回放（独立复验，不采信自述）

**验证方法**：在 Node `vm` 中加载 **真实的** `demo-data.js` + `screens.js`（只在 IIFE 结尾注入 1 行导出钩子，被测函数体 100% 原样），用最小 DOM/UI 桩调用**真实** `sendDemoQuery`，并**加载真实 CHAT_DEMOS 数据**。脚本：`.tmp-qa/qa_h2_offline.js`（**失败项: 0**）。

### B1 未收录问题：`今天食堂吃什么`

```
✅ flow.status === "error"                    — 实际 error
✅ 存在 error 消息
✅ error 文案含「未收录离线回放」
✅ error 文案含「请启用在线模式」
✅ 未出现任何结果卡（不静默回放）
✅ 渲染 HTML 中不含「停线 / 复线 / SMT-1」字样
✅ flow 未执行任何节点
✅ 未生成 HITL 卡片
```
提示原文：`后端离线：该问题未收录离线回放（离线回放覆盖 5 个快捷问题）。请启用在线模式（起后端服务）运行真实链路，或点击上方 5 个快捷问题之一。`
→ **H-2 已彻底修复**，不会再"问食堂 → 回答停线"。

### B2 5 个 chip 逐一离线回放 → 场景对应

| chip | 回放结果卡 `scenario_key` | 执行节点数 | flow |
|---|---|---|---|
| A型号在SMT-1停线，何时复线？ | `line_stop_v2` | 11 | done |
| 今天锡膏批次有问题 | `incoming_quality_v2` | 9 | done |
| 华为投诉BATCH-A-1008 | `customer_complaint_v2` | 11 | done |
| A型号能发多少货 | `delivery_status_v2` | 12 | done |
| SMT-1设备参数异常吗 | `equipment_warning_v2` | 10 | done |

三者一致（chip 文本 → `CHAT_DEMOS` key → `scenario_key`）：**5 个 chip 回放 5 个不同场景，无"全部回放停线"** ✅ 且全部 `flow=done`、无 error 消息。

### B3 附带验证：trim 容错

`"  华为投诉BATCH-A-1008  "`（首尾空格）→ 仍命中 `customer_complaint_v2` ✅

### B4 在线模式仍走真实链路

起服务 `:8007`，5 个 chip 全打一遍：全部 `http=200`（首请求 2.71s=探针 1.5s 预热，其余 0.15–0.27s），返回 `degraded=true` 的真实编排结果（非 demo）。抽查 S3 明细：`scenario_key=customer_complaint_v2`、8D D1–D8、双 HITL `waiting_hitl`、`data_gap=1`，与 Round 1 一致 ✅

---

## C. H-1 data_gap 事实核验（验证"真实缺口=1"是否成立）

### C1 先查库确认 S3 的非缺口数据**确实都有**（否则缺口应 >1）

| 数据域 | 库内事实 | 是否缺口 |
|---|---|---|
| 客诉单（D2） | `customer_complaint(BATCH-A-1008)` = 1 行，华为终端 | 有数据 |
| PQE 过程不良（D4） | `quality_defect(BATCH-A-1008)` = 1 行（贴片偏移 46） | **有数据 → 不算缺口** |
| 供应商侧 | `material_batch(BATCH-A-1008)` = 1 行（深南电路） | **有数据 → 不算缺口** |
| 业务对客 | `shipment(A型号)` = 2 行（2000 / 1500） | **有数据 → 不算缺口** |
| 工程根因 | 库中**无** bom / ecn / drawing / plm / process_param 任何表 | **唯一真实缺口** |

→ **"S3 真实缺口 = 1"裁定成立** ✅（原 2 条确系同一 note 在 `rd_engineering` + `report` 重复写入）

### C2 S3 provenance

```
[1 条] domain='rd_engineering'  note='设计根因：无 BOM/ECN/图纸数据源，未编造结论'
✅ 条数 == 1   ✅ domain 全为 rd_engineering   ✅ 无 report 域冗余   ✅ note 无重复   ✅ 含「设计根因」
✅ internal_brief['数据缺口'] 仅 1 条（无分号拼接）
```

### C3 S8 provenance

```
[2 条] domain='rd_engineering' note='工艺参数基线：无数据源，未编造结论'
       domain='supply_chain'   note='备件库存：无数据源，未编造结论'
✅ 条数 == 2   ✅ domain 集合 == {rd_engineering, supply_chain}   ✅ 无 report 域   ✅ note 唯一
```
在线 HTTP 返回结果同口径：S3 `gaps=1`、S8 `gaps=2`、其余 3 场景 `gaps=0`。

### C4 UI 横幅渲染（把真实 HTTP 返回喂给真实 `resultCardHtml`）

脚本 `.tmp-qa/qa_c4_banner.js`（加载真实 `screens.js`）：

| 场景 | 横幅次数 | 溯源缺口行 | 每条 note 渲染次数 |
|---|---|---|---|
| S3 | 1 | 1（== 唯一 note 数 1） | 3 |
| S8 | 1 | 2（== 唯一 note 数 2） | 3 |

"3 次"分别是**横幅 1 + 简报「数据缺口」项 1 + 溯源行 1，三处各一次**，不是重复渲染；横幅拼接段条目数分别 = 1 / 2，**无重复文案** ✅
→ **UI 层在 S3/S8 正常渲染且无重复显示**。

---

## D. H-3 README 示例（原样拷贝实际跑通）

方法：正则取出 README 指南三的 `yaml` 代码块 → 落盘为临时场景目录里的 `material_shortage_v2.yaml`（连同 5 个既有场景）→ 用真实 `V2Runtime` 装配 → 真实 `orchestrator.handle("缺料了，齐套吗？")`。

```
✅ D1 临时场景注册成功（YAML 解析无异常）
   运行结果: scenario_key=material_shortage_v2  error=None
   timings: ['intake', 'scan', 'planning', 'procurement', 'dispatch', 'urgent_po']
✅ D2 运行无 error（无 KeyError: 未注册 handler）
✅ D3 命中 material_shortage_v2
✅ D4 HITL 复用 hitl_delivery_commit —— prompt=加急采购决策（金额>5万需采购总监确认）/ 审批人=[采购总监]（step 参数覆盖生效）
✅ D5 workflow 复用 delivery_risk_flow
```
→ **示例可直接运行**（含并行 fan_out、workflow、hitl 全链），H-3 已修复 ✅

措辞与事实一致性核对：现文写"新场景 = 新 YAML + **复用已注册 handler key**，不改框架代码"，并明确补充"**若要语义专属的新 workflow id 或新 HITL kind，需在 `node_registry` 注册一次（+1 行）**，那就不再是纯零代码，请如实说明" → **与实际行为一致**（本轮已实测：未注册 key 会在 `yaml_dag.py:244` 抛 KeyError，注册后即可复用）。

> 备注（非缺陷）：该示例没有 report 节点，因此 `internal_brief={}`；示例定位是最小可跑骨架，符合预期。

---

## E. G 探针与 H-4

- **探针单测**：`6 passed`；覆盖三态（服务可达+模型齐 / 模型缺失 / 不可达）+ HTTP 非 200 + 名称归一化；**变异验证**证明"去掉模型校验 → 2 条测试失败"（见 §A3）✅
- **探针实现**：`_probe_ollama` 同时校验 L1/L2 两级模型（任一缺失 → False，走 degraded），`_normalize_model_name` 处理无 tag → `:latest`，异常一律 False；`_probe_openai` 校验 `OPENAI_MODEL` 存在 ✅
- **README 已知限制第 7 条**（`VectorStore` 签名与 Chroma 适配器不一致 / 接 Milvus 前需收敛）**已存在** ✅（H-4 已登记，未改实现，符合"只留位不接线"边界）

---

## F. Round 2 遗留（已清零）

| # | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| ~~R2-1~~ | ~~低~~ | `tests/v2/test_qa_v1_equivalence.py::_skip_if_llm_not_ready` | hint 含裸 `"model"`/`"ollama"`，导致 message 含这两个词的 `AssertionError` 被 skip 而非失败 | **已修复并复验**（`b1ec49d`，见 §I） |

Round 1 的 4 条已知问题（H-1/H-2/H-3/H-4）**处置状态**：

| # | 状态 | 证据 |
|---|---|---|
| H-1 data_gap 重复 | **已修复** | §C2/C3：S3=1 条 rd_engineering、S8=2 条且 domain 正确、note 唯一 |
| H-2 离线静默兜底 | **已修复** | §B1：未收录 → error + 需在线模式提示，无停线结论，HTML 无"停线/SMT-1" |
| H-3 README 示例不可运行 | **已修复** | §D：原样拷贝跑通，措辞与事实一致 |
| H-4 VectorStore 签名 | **已登记未改实现**（符合边界） | README 已知限制第 7 条 |

---

## G. Round 2 现场还原

- 未修改任何源码：探针变异（`return bool(installed)`）已还原，`git diff src/mfg_agents/tools/llm_factory.py` 为空。
- 临时场景目录、临时工作流 DB 均在脚本内 `mkdtemp` + `shutil.rmtree` 清理；未污染 `data/mfg_agent.db`（已核对与 HEAD 无差异）。
- 取证据脚本：`.tmp-qa/qa_h2_offline.js`（H-2 离线）、`.tmp-qa/qa_round2_cd.py`（data_gap + README 示例）、`.tmp-qa/qa_c4_banner.js`（UI 横幅）、`.tmp-qa/qa_r21_integration.py`（R2-1 集成级）。

## H. Round 2 判定

| 判定项 | 结论 |
|---|---|
| 源码 Bug（需回退工程师） | **无** |
| 测试软化（需 QA 自查修正） | **无**；测试改动全部为加强；R2-1 已由工程师按方案 A 修复并通过复验 |
| 可交付性 | **可交付** |

**Round 2 验收结论：修复有效，308 passed / 2 skipped / 0 failed，V2 增量最终通过 QA 验收。**

---

## I. R2-1 复验（commit `b1ec49d`，QA 独立取证）

### I1 改动范围核对（`git diff 3f95721 b1ec49d`）

```
 tests/v2/test_qa_v1_equivalence.py | 8 +++++++-
 1 file changed, 7 insertions(+), 1 deletion(-)
```
**只改了 1 个文件、只动了 `_skip_if_llm_not_ready` 一个函数**（+ 守卫 2 行 + docstring 4 行 + 空行），**无任何其他夹带**：未改 `src/`、未改其他测试文件、未改配置。补丁内容：

```python
+    R2-1：**AssertionError 必须先 re-raise**——否则 hint 中的裸子串 "model"/"ollama"
+    会把 `AssertionError("...model...")` 误判为"LLM 不可用"而 skip，吞掉真实断言失败。
+    """
+    if isinstance(exc, AssertionError):
+        raise exc
     msg = str(exc).lower()
```
实现与建议方案 A 一致，且**守卫置于 `hint` 匹配之前**（顺序正确）。

### I2 异常矩阵（14 例，独立构造，全部符合预期 14/14）

| 输入异常 | 期望 | 实际 |
|---|---|---|
| `AssertionError("state 缺少 model 字段")` ← 上轮漏网 1 | raise | **raise** ✅ |
| `AssertionError("ollama 场景断言失败")` ← 上轮漏网 2 | raise | **raise** ✅ |
| `AssertionError("assert final.get('customer_report') 失败")`（普通） | raise | raise ✅ |
| `AssertionError()`（空 message） | raise | raise ✅ |
| `AssertionError("model 'qwen3.5:9b' not found")`（**守卫须先于 hint**） | raise | **raise** ✅ |
| `AssertionError("connection refused")` | raise | raise ✅ |
| `CustomAssert("custom: model layer mismatch")`（**AssertionError 子类**） | raise | raise ✅ |
| `ConnectionError("connection refused")` | skip | skip ✅ |
| `RuntimeError("model 'qwen3.5:9b' not found, try pull")` | skip | skip ✅ |
| `TimeoutError("timed out")` | skip | skip ✅ |
| `OSError("Connection refused")` | skip | skip ✅ |
| `ValueError("配方错误")` | raise | raise ✅ |
| `KeyError("scenario")` | raise | raise ✅ |
| `TypeError("unsupported operand")` | raise | raise ✅ |

关键点：**即使 message 里出现 "model not found"/"connection refused"，只要是 `AssertionError` 就一律 re-raise**（守卫优先级正确）；`isinstance` 对子类同样生效。

### I3 集成级复验（驱动**真实**测试函数体，6/6）

不只测 helper 本身 —— 直接把 `test_qa_v1_equivalence.py` 里两个 `@requires_llm` 测试的 `app.invoke/stream` 换成抛异常的桩，**调用真实测试函数**观察最终结果：

| 桩抛出的异常 | 期望 | 实际（两个测试函数各一次） |
|---|---|---|
| `AssertionError("state 缺少 model 字段")` | AssertionError 上抛（pytest 记为 **FAILED**） | `AssertionError: state 缺少 model 字段` ✅ ×2 |
| `AssertionError("ollama 场景断言失败")` | 同上 | `AssertionError: ollama 场景断言失败` ✅ ×2 |
| `RuntimeError("model 'qwen3.5:9b' not found, try pull")` | 转 skip | `Skipped: LLM 运行期不可用（探针漏网按跳过处理）…` ✅ ×2 |

→ **真实断言失败不会再被吞成 skip；环境类异常仍按离线跳过** ✅（脚本 `.tmp-qa/qa_r21_integration.py`）

### I4 单文件回归

```
pytest tests/v2/test_qa_v1_equivalence.py -q
→ exit=0 | 1 passed, 2 skipped
   SKIPPED ...:44 / :61  V1 图执行需真实 LLM（Ollama），离线跳过   （行号因 helper 增长由 26/39 顺移）
   3 tests collected
```
行为与修复前一致（离线仍 2 skip），无新增失败 ✅

### I5 R2-1 结论

**已修复并复验通过。** 修复精准（1 文件 / 1 函数 / 守卫前置）、行为正确（AssertionError 全 re-raise、环境异常仍 skip）、无副作用（单文件 1 passed 2 skipped、未夹带其他改动）。Round 2 遗留项**清零**。
