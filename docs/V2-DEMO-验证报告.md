# V2 可接入框架 · Demo 验证报告

| 项 | 内容 |
|---|---|
| 验证人 | 寇豆码（Kou，Software Engineer） |
| 验证时间 | 2026-10-16（本地环境） |
| 验证环境 | Windows / Python 3.13（uv 管理 .venv）/ **Ollama 未启动（degraded 模式）** / 无外网 |
| 测试基线 | `uv run pytest tests/ -q` → **112 passed**（含 V1 回归 + tests/v2/ 全部新增） |
| git 基线 | `v1.0-prototype`（V1 原型基线 tag，改代码前已提交） |

> 本报告所有终端输出均为**真实运行结果**（非构造），degraded 模式为硬性验收场景：
> LLM 不可用时路由降级规则、FA/报告模板化，全链路功能保持可用。

---

## 1. CLI Demo（全链路事件流 + 简报 + provenance + degraded 标记）

```bash
$ uv run python -m mfg_agents.v2.demo.cli "A型号在SMT-1停线，何时复线？"
```

```text
============================================================
 V2 可接入框架 · 统一编排 Demo（mfg_agent_platform.v2）
============================================================
问题：A型号在SMT-1停线，何时复线？
LLM 可用性：不可用（degraded 降级模式）
▶ 18:27:06 business
  · 18:27:06   [意图识别]      两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）
  · 18:27:06   [意图识别]      识别场景：line_stop | 型号：A型号 | 线体：SMT-1
✓ 18:27:06 business          识别场景：line_stop | 型号：A型号 | 线体：SMT-1 [1ms]
▶ 18:27:06 confirm_stop
  · 18:27:06   [SQL查询]      查询SMT-1线A型号今日OEE与产出 + 进行中停线事件
  · 18:27:06   [SQL查询]      返回2条产出，1条停线事件（原文）
✓ 18:27:06 confirm_stop      SMT-1线A型号今日OEE=32.1% [3ms]
▶ 18:27:06 equipment
▶ 18:27:06 quality
▶ 18:27:06 supply_chain
  · 18:27:06   [SQL查询]      查询SMT-1线A型号不良分布
  · 18:27:06   [SQL查询]      查询SMT-1线设备异常
  · 18:27:06   [SQL查询]      查询今日到货批次
  · 18:27:06   [SQL查询]      返回2种不良
✓ 18:27:06 quality           主导不良：锡膏偏移，共77pcs [3ms]
  · 18:27:06   [SQL查询]      返回3条异常
✓ 18:27:06 equipment         近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5） [4ms]
  · 18:27:06   [SQL查询]      返回1条批次（敏感字段已按角色脱敏: True）
✓ 18:27:06 supply_chain      今日1个批次到货，无来料异常 [4ms]
⇉ 18:27:06 fanin             汇聚 5 域结果
▶ 18:27:06 fa
  · 18:27:06   [RAG检索]      查询: SMT-1线 锡膏偏移 设备告警=E-201,E-205,E-301
  · 18:27:07   [RAG检索]      匹配3条历史案例
✓ 18:27:07 fa                评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论） [536ms]
▶ 18:27:07 impact_wf
✓ 18:27:07 impact_wf         工单 wf-07853f21e584 已创建（P1 通知 计划,物控,业务，SLA 2h） [68ms]
▶ 18:27:07 hitl_restart
✓ 18:27:07 hitl_restart      复线决策确认：是否按 FA 建议 ETA 复线？ → waiting_hitl（审批人：制造现场负责人） [60ms]
▶ 18:27:07 hitl_eta
✓ 18:27:07 hitl_eta          对客 ETA 承诺确认：确认回复客户的预计恢复时间？ → waiting_hitl（审批人：业务经理） [33ms]
▶ 18:27:07 report
✓ 18:27:07 report            对客报告已生成（degraded 模板化 + 禁词表兜底） [0ms]

---------- 路由结果（两级路由） ------------------------------------
level=L0  scenario=line_stop_v2  domain=manufacturing  confidence=1.0  low_confidence=False
依据：场景触发词命中: line_stop_v2
实体：{"product_model": "A型号", "line_code": "SMT-1", "urgency": "normal", "v1_scenario": "line_stop"}

---------- 最终简报（内部） --------------------------------------
  停线确认: True
  生产: SMT-1线A型号今日OEE=32.1%
  品质: 主导不良：锡膏偏移，共77pcs
  供应链: 今日1个批次到货，无来料异常
  设备: 近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）
  FA评级: 评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论）
  仓储:

---------- 对客报告（脱敏） --------------------------------------
尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核与工艺参数优化。根据当前评估，预计约 2 小时恢复，届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。

---------- HITL 待确认（不自动放行） -------------------------------
  1. [waiting_hitl] 复线决策确认：是否按 FA 建议 ETA 复线？ （审批人：制造现场负责人）
     工单: wf-1a06a9fa7625  操作: 确认复线方案 / 暂缓复线
  2. [waiting_hitl] 对客 ETA 承诺确认：确认回复客户的预计恢复时间？ （审批人：业务经理）
     工单: wf-25b850f751a4  操作: 确认对客ETA / 修改ETA

---------- 工作流工单（确定性流程） ----------------------------------
  wf-07853f21e584 [running] 停线影响评估工单 | 受理: 计划,物控,业务 | SLA: 2026-10-16T12:27:07+00:00
     通知: P1 → 计划,物控,业务

---------- Provenance（可审计：SQL 原文 + 命中行 + 节点耗时） -----------
  [equipment] registered: equip_alarms
      SQL: SELECT equipment_id, alarm_code, param_name, param_value FROM equipment_log WHERE line_code = :line AND is_abnormal = 1
      命中: 3 行 | 表: ['equipment_log'] | 脱敏: False
  [manufacturing] registered: mfg_oee
      SQL: SELECT record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min FROM production_output WHERE line_code = :line AND product_model = :model AND record_date = '2026-10-15'
      命中: 2 行 | 表: ['production_output'] | 脱敏: False
  [manufacturing] registered: mfg_stop_ongoing
      SQL: SELECT * FROM line_stop_event WHERE line_code = :line AND product_model = :model AND status = 'ongoing'
      命中: 1 行 | 表: ['line_stop_event'] | 脱敏: False
  [quality] registered: quality_defect_dist
      SQL: SELECT defect_type, SUM(defect_qty) AS total_qty FROM quality_defect WHERE line_code = :line AND product_model = :model GROUP BY defect_type
      命中: 2 行 | 表: ['quality_defect'] | 脱敏: False
  [supply_chain] registered: supply_batches_today
      SQL: SELECT batch_no, part_no, part_name, supplier, incoming_result FROM material_batch WHERE date(arrival_time) = '2026-10-15'
      命中: 1 行 | 表: ['material_batch'] | 脱敏: True
  [fa] rag: SMT-1线 锡膏偏移 设备告警=E-201,E-205,E-301
  [hitl] hitl: wf-25b850f751a4
  [workflow] workflow: wf-07853f21e584
  [节点] business: 1.2ms (ok)      [节点] confirm_stop: 2.9ms (ok)
  [节点] equipment: 4.1ms (ok)     [节点] quality: 2.7ms (ok)
  [节点] supply_chain: 3.6ms (ok)  [节点] fa: 536.2ms (ok)
  [节点] impact_wf: 68.3ms (ok)    [节点] hitl_restart: 59.6ms (ok)
  [节点] hitl_eta: 32.6ms (ok)     [节点] report: 0.4ms (ok)

---------- 降级标记 ------------------------------------------
  degraded=True（FA 分析 LLM 不可用：模板化结构化结论; 报告生成 LLM 不可用：模板化 + 禁词表兜底）

总耗时: 755ms | run_id: run-a697e4d69c6f
```

**验收要点核对**：事件流（V1 兼容枚举）✓ 简报 ✓ 对客脱敏报告 ✓ 两个 HITL 节点
均为 `waiting_hitl` 且不自动放行 ✓ 工作流工单 running + P1 通知 ✓ provenance
（SQL 原文 + 命中行 + 脱敏标记 + 10 节点耗时）✓ degraded 标记与原因 ✓

**未停线分支**（等价 V1 "没停线直接回复"）：

```bash
$ uv run python -m mfg_agents.v2.demo.cli "B型号在DIP-2停线了吗，何时复线？"
```

```text
  停线确认: False
  生产: DIP-2线B型号今日OEE=84.5%
---------- HITL 待确认（不自动放行） -------------------------------
  （无待确认项）
---------- Provenance（可审计：SQL 原文 + 命中行 + 节点耗时） -----------
  [manufacturing] registered: mfg_oee        命中: 1 行 | 表: ['production_output']
  [manufacturing] registered: mfg_stop_ongoing 命中: 0 行 | 表: ['line_stop_event']
  [节点] business: 0.9ms (ok)   [节点] confirm_stop: 2.4ms (ok)   [节点] report: 0.3ms (ok)
```

fan_out 条件 `confirmed_stop=False` → 直接跳转 report，品质/供应链/设备未执行
（与 V1 路由行为一致）。

---

## 2. FastAPI Demo（/api/v2/query JSON + SSE 事件样例）

```bash
$ IM_TOKEN=QDG6eK IM_AES_KEY=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG \
  IM_APPID=wx5823bf96d3bd56c7 \
  uv run python -m uvicorn mfg_agents.v2.demo.api:app --port 8005
```

### 2.1 POST /api/v2/query（统一入口，TwoLevelRouter → YamlDagExecutor）

```bash
$ curl -s -X POST http://127.0.0.1:8005/api/v2/query \
    -H "Content-Type: application/json" \
    -d '{"query":"A型号在SMT-1停线，何时复线？","request_id":"demo-req-rpt"}'
HTTP 200 (2.264390s)
```

响应 JSON（截取关键字段）：

```json
{
  "run_id": "run-8813d14fb331",
  "route": {
    "domain": "manufacturing",
    "scenario_key": "line_stop_v2",
    "confidence": 1.0,
    "level": "L0",
    "entities": {"product_model": "A型号", "line_code": "SMT-1",
                 "urgency": "normal", "v1_scenario": "line_stop"},
    "low_confidence": false,
    "reason": "场景触发词命中: line_stop_v2"
  },
  "scenario_key": "line_stop_v2",
  "internal_brief": {
    "停线确认": true,
    "生产": "SMT-1线A型号今日OEE=32.1%",
    "品质": "主导不良：锡膏偏移，共77pcs",
    "供应链": "今日1个批次到货，无来料异常",
    "设备": "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）…",
    "FA评级": "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））…预计2h复线（degraded：LLM 不可用，模板化结论）"
  },
  "report": "尊敬的客户：目前产线处于计划内暂停（例行校准）…预计约 2 小时恢复…",
  "hitl": [
    {"kind": "restart", "prompt": "复线决策确认：是否按 FA 建议 ETA 复线？",
     "status": "waiting_hitl", "approver_roles": ["制造现场负责人"],
     "actions": ["确认复线方案", "暂缓复线"]},
    {"kind": "eta", "prompt": "对客 ETA 承诺确认：确认回复客户的预计恢复时间？",
     "status": "waiting_hitl", "approver_roles": ["业务经理"],
     "actions": ["确认对客ETA", "修改ETA"]}
  ],
  "workflow_tasks": [
    {"task_uid": "wf-60ab8c24efd3", "status": "running",
     "title": "停线影响评估工单", "assignees": ["计划", "物控", "业务"],
     "sla_due_at": "2026-10-16T12:28:03+00:00", "notify": "P1"}
  ],
  "degraded": true,
  "degraded_reasons": ["FA 分析 LLM 不可用：模板化结构化结论",
                       "报告生成 LLM 不可用：模板化 + 禁词表兜底"],
  "timings": {"business": 0.6, "confirm_stop": 2.5, "quality": 2.2,
              "supply_chain": 2.3, "equipment": 2.4, "fa": 2010.6,
              "impact_wf": 60.3, "hitl_restart": 29.3, "hitl_eta": 42.1,
              "report": 0.4},
  "provenance_sample": [
    {"channel": "registered", "query_name": "mfg_oee",
     "tables": ["production_output"], "row_count": 2, "masked": false,
     "sql": "SELECT record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min FROM production_output WHERE line_code = :line AND product_model = :model AND record_date = '2026-10-15'",
     "domain": "manufacturing"}
  ]
}
```

### 2.2 GET /api/v2/events（SSE，请求级队列，修 D6 并发串话）

```bash
$ curl -s "http://127.0.0.1:8005/api/v2/events?request_id=demo-req-rpt"
```

```text
data: {"type": "agent_start", "node": "business", "label": "业务Agent · 意图识别", "icon": "🧠"}

data: {"type": "agent_end", "node": "business", "label": "业务Agent · 意图识别", "summary": "识别场景：line_stop | 型号：A型号 | 线体：SMT-1", "status": "ok", "elapsed_ms": 0.6}

data: {"type": "agent_start", "node": "confirm_stop", "label": "制造Agent · 确认停线", "icon": "🗄️"}

data: {"type": "agent_end", "node": "confirm_stop", "label": "制造Agent · 确认停线", "summary": "SMT-1线A型号今日OEE=32.1%", "status": "ok", "elapsed_ms": 2.5}

data: {"type": "agent_start", "node": "equipment", "label": "设备Agent · 查EAM", "icon": "⚙️"}

data: {"type": "agent_start", "node": "quality", "label": "品质Agent · 查QMS", "icon": "🔬"}
```

事件枚举（agent_start/tool_start/tool_end/llm_start/agent_end/fan_in/final/error）
与 V1 前端完全一致；每个请求绑定独立队列（contextvars + EventHub 按 request_id
寻址），不再有全局单队列的并发串话。

### 2.3 POST /api/v2/im/wecom/callback（企业微信回调）

```bash
--- GET URL 校验（返回明文 echostr）---
echostr_plain_123

--- POST 消息回调（加密回包，前 300 字符）---
HTTP 200 (2.001910s)
<xml><Encrypt><![CDATA[cK6O1kiOhbnZkFedSob56ZhM2kKu6+MO6kcNTaWnJGVRev/Krq9rdsClnMnQ/2LxRM+W6V4VyxjYN0pfu+aKQTRnlyfm+b23cxpwamvYtTBL555TtVA/mQRiwIsa0tUENQfs16KZQTubvdwiBPhBIp6kNmFizIDgn4otlt5m0viXyD604pPvygpQRtEBNJMV813W8F5prk9DgWEod5o4hBFzr0vyVShgwwJfyWx+CDpqosW7sk1TdSrPSbKG0myU1sHXdlBg0sg1vEKcaFyA7…

--- 回包解密验证 ---
回复签名自验证: True
<xml><ToUserName><![CDATA[zhangsan]]></ToUserName><FromUserName><![CDATA[wx5823bf96d3bd56c7]]></FromUserName><CreateTime>1792146539</CreateTime><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[【停线应急协同 · 分析简报】
尊敬的客户：目前产线处于计划内暂停（例行校准）…预计约 2 小时恢复…

待人工确认：
- 复线决策确认：是否按 FA 建议 ETA 复线？（制造现场负责人）
- 对客 ETA 承诺确认：确认回复客户的预计恢复时间？（业务经理）

⚠ degraded 模式：LLM 不可用，结论
```

---

## 3. IM 模拟器（离线：回调解密 → 全链路 → 消息卡片）

```bash
$ uv run python -m mfg_agents.v2.demo.im_simulator
```

```text
============================================================
 V2 · 企业微信 IM 模拟器（离线，真实回调协议格式）
============================================================

---------- 1. 构造企业微信回调（加密 + 签名） --------------------------
echostr/encrypt 长度: 472（AES-256-CBC, PKCS#7@32B）
msg_signature: 6fec9cad7e7330b1c5c7b22934e50d40b6531506

---------- 2. GET URL 校验（echostr + SHA1 验签） --------------
验签通过，解密 echostr -> 'echo_plain_text'

---------- 3. POST 消息回调（验签 → 解密 → InboundMessage） --------
verify_callback: True
InboundMessage: user_id=zhangsan conversation_id=wx5823bf96d3bd56c7 text='A型号在SMT-1停线，何时复线？' channel=wecom

---------- 4. 安全负向用例（篡改签名/密文必须拒绝） ------------------------
篡改签名 verify_callback: False（期望 False）
篡改密文解密被拒绝: WeComCryptoError: 解密结果不是合法 UTF-8（密文可能被篡改）: 'utf-8' codec can't decode byte 0xc7 in position 157: invalid continuation byte

---------- 5. 全链路编排（TwoLevelRouter → YamlDagExecutor） ----
LLM 可用性：不可用（degraded）
  [agent_start] business
  [agent_end] business 识别场景：line_stop | 型号：A型号 | 线体：SMT-1
  [agent_start] confirm_stop
  [agent_end] confirm_stop SMT-1线A型号今日OEE=32.1%
  [agent_start] equipment / quality / supply_chain   （三域并行 fan-out）
  [agent_end] quality 主导不良：锡膏偏移，共77pcs
  [agent_end] equipment 近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）…
  [agent_end] supply_chain 今日1个批次到货，无来料异常
  [fan_in] fanin 汇聚 5 域结果
  [agent_end] fa 评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论）
  [agent_end] impact_wf 工单 wf-b8cdaf3acb1f 已创建（P1 通知 计划,物控,业务，SLA 2h）
  [agent_end] hitl_restart 复线决策确认… → waiting_hitl（审批人：制造现场负责人）
  [agent_end] hitl_eta 对客 ETA 承诺确认… → waiting_hitl（审批人：业务经理）
  [agent_end] report 对客报告已生成（degraded 模板化 + 禁词表兜底）

---------- 6. 消息卡片效果（ReplyCard） --------------------------
标题: 停线应急协同 · 分析简报
正文:
尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核与工艺参数优化。根据当前评估，预计约 2 小时恢复，届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。

待人工确认：
- 复线决策确认：是否按 FA 建议 ETA 复线？（制造现场负责人）
- 对客 ETA 承诺确认：确认回复客户的预计恢复时间？（业务经理）

⚠ degraded 模式：结论为规则/模板化输出，请人工复核。
按钮: ['确认复线方案', '暂缓复线', '确认对客ETA', '修改ETA']

---------- 7. 被动回复 XML（加密回包，可解密验证） -----------------------
回复体长度: 1567 字符
回复签名自验证: True
解密回复明文（前 200 字）:
<xml><ToUserName><![CDATA[zhangsan]]></ToUserName><FromUserName><![CDATA[wx5823bf96d3bd56c7]]></FromUserName><CreateTime>1792146436</CreateTime><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[【停线

---------- 8. 主动推送（push，预留通道） ----------------------------
  push -> manager-li: 停线影响评估待处理

---------- 汇总 --------------------------------------------
事件数: 33 | HITL: 2 | 工单: 1 | degraded: True
总耗时: 693ms
```

---

## 4. 测试结果

```bash
$ uv run pytest tests/ -q
......................................................................... [ 64%]
........................................................................ [100%]
112 passed in 13.01s
```

| 测试文件 | 覆盖 | 数量 |
|---|---|---|
| `tests/test_v1_regression.py` | V1 图可编译 / 五场景路由 / SQL 只读守卫 / 四域结论 | 10 |
| `tests/v2/test_aes_cbc.py` | AES 纯标准库实现：FIPS-197 + NIST CBC 已知向量、round-trip | 6 |
| `tests/v2/test_wecom_crypto.py` | 企微签名已知向量 / 加解密 round-trip / appid 与篡改拒绝 / 回包自验证 | 14 |
| `tests/v2/test_router.py` | L0 快路径 / LLM 不可用降级 / 低置信标记 / 实体抽取 | 6 |
| `tests/v2/test_context_and_state.py` | SharedContext 只增不改 / 并发写入 / reducer 域隔离 | 11 |
| `tests/v2/test_scenario_registry.py` | 场景加载 / fail-fast ×5 / 数据源只读边界 / 行数上限 | 15 |
| `tests/v2/test_text2sql.py` | 指标库 / 注册查询 / 审核链拦截 / 表权限 / 脱敏 / 人工兜底 | 16 |
| `tests/v2/test_yaml_dag.py` | 5 节点类型 / 并行 fan-out / 条件跳转 / 超时降级与 abort / 安全求值 | 13 |
| `tests/v2/test_workflow.py` | 状态机 / HITL 待办 / SLA / 持久化 / 非法迁移拦截 | 13 |
| `tests/v2/test_e2e_v2.py` | 全链路验收 / degraded 标记 / V1 等价 / 未停线分支 / 超范围兜底 | 8 |

## 5. 验收标准对照（Definition of Done）

| # | 验收项 | 结果 |
|---|---|---|
| 1 | 文件结构全部落盘，`uv run pytest tests/ -q` 全绿（V1 回归 + tests/v2/） | ✅ 112 passed |
| 2 | 三个 demo 各跑通一次，真实终端输出入档 | ✅ 本文档 §1/§2/§3 |
| 3 | 企微适配器签名/解密逻辑单测覆盖（构造已知向量） | ✅ test_wecom_crypto.py 14 例（含 NIST/FIPS 锚定的 AES 实现） |
| 4 | `src/mfg_agents/v2/README.md` 三段接入指南 | ✅ 新 IM / 新数据源 / 新场景 |
| 5 | 全局一致性审查 | ✅ 见下方结论 |
| 6 | git 提交（V2 实现一个 commit） | ✅ 见 git log |

## 6. 备注与遗留

1. **V1 测试脚本的收集隔离**：V1 的 5 个 `tests/test_*.py` 是演示脚本（顶层直接
   执行、依赖 Ollama/运行中服务），在 pytest 收集期即报错（基线即如此）。
   处理：`tests/conftest.py` 用 `collect_ignore` 跳过收集（V1 文件零改动，
   仍可 `uv run python tests/test_e2e.py` 手动运行），等价覆盖由
   `tests/test_v1_regression.py`（离线 pytest 化）承接。
2. **环境修复**：`.venv` 原先未装 pytest（`uv run pytest` 实际命中系统
   Python 3.11 的 pytest，导致 `mfg_agents` 不可导入）。已将 pytest 加为
   项目 dev 依赖（`uv add --dev pytest`）。
3. **OTD 指标口径**：演示库无"订单要求日/实际发货日"两字段，指标库中 OTD 以
   `shipment.eta` 为客户要求日的演示口径实现（已在 `data/metrics/otd.yaml`
   desc 中注明），真实数据就绪后升级定义。
4. **V2 对脱敏的行为差异**：V2 供应链查询对非采购角色脱敏 supplier 字段
   （架构 §2.1 要求"供应商名对制造角色脱敏"），结论结构与 V1 一致，
   差异为有意的安全加固（单测锁定）。
