# V2 前端展示应用 · QA 验收报告

| 项 | 内容 |
|---|---|
| 验收人 | 严过关（Yan，QA Engineer） |
| 验收对象 | commit `924c5d0` 前端展示应用（`src/mfg_agents/v2/demo/ui/` + `demo/api.py` 增量） |
| 验收时间 | 2026-10-16 21:00–21:20（本机） |
| 验收方式 | **独立自起服务**（工程师"服务器仍在 8004 运行"的声明实测失效：连接拒绝；8004 实际空闲，由 QA 按文档命令启动）+ Playwright/chromium 浏览器自动化 + curl + SQLite 库级直查 |
| **总结论** | **通过，可交付**。A/B/C/D 四大项全部通过；E 项 4 条已知限制全部裁判为"接受"；发现 1 项非阻塞观察项 + 1 项工程师声明不精确（均已记录，无需返工）。 |

---

## 1. 通过 / 失败清单

| # | 验收项 | 结果 | 关键证据 |
|---|---|---|---|
| A1 | 按文档命令自起服务，`/demo-ui/` 200 且 HTML 完整 | ✅ 通过 | HTTP 200，3994 bytes，六屏导航/顶栏/脚本齐全；`/demo-ui`→307 |
| A2 | 测试套件无回归 | ✅ 通过 | `uv run pytest tests/ -q` → **280 passed, 2 skipped, 0 failed**（与声明一致） |
| A3 | 三个新端点行为正确 | ✅ 通过 | pending 返回真实工单（waiting_hitl/context/history/sla_due_at）；approve/reject 不存在 task_id → 404 `{"detail":"工单不存在: 'wf-nonexistent-999'"}`；events/open → 200 |
| B1 | 问数全链路（SSE 事件流完整渲染） | ✅ 通过 | 页面收到 **33 事件 / 11 节点**：business→confirm_stop→三域并行（3 列）→fanin→fa→impact_wf→hitl×2→report；评级 pill **B**；provenance 19 条（5 条 SQL 原文 + 命中行 + 脱敏标记）；degraded 徽标+横幅（Ollama 离线已证实） |
| B2 | HITL 真实状态变更 | ✅ 通过 | 同意：DB `waiting_hitl→running`，history 留痕 `approve actor=张启明 comment=门户审批：同意`；横幅"已同意 · 工单状态 running · 处理人 张启明"；按钮移除；pending 移除。驳回：DB `→closed`，`reject actor=张启明` |
| B3 | 人工兜底路径 | ✅ 通过 | "今天锡膏批次有问题"→ 人工兜底工单卡，明示"**人工兜底是平台设计的一部分**" |
| B4 | SSE 不丢事件 | ✅ 通过 | 后端独立订阅实测推送 **33 事件**（10×agent_start + 1×fan_in）= 页面收到 33，**零丢失** |
| C1 | 停后端 + file:// 直开降级 | ✅ 通过 | curl 证实后端已停（连接拒绝 + 502）；file:// 六屏完整渲染无白屏；"后端离线 · 演示模式"顶栏 + 环境标识 + toast |
| C2 | 问数演示回放可用且标注演示 | ✅ 通过 | 11 节点逐节点动画 + "演示回放"徽标；演示模式 SSE 连接数 = 0 |
| C3 | HITL 演示审批明示"未真实调用后端" | ✅ 通过 | 横幅 + toast 双重明示"（演示模式，未真实调用后端）" |
| D1 | 六屏逐一渲染 | ✅ 通过 | 工作台（角色切换/待办/预警摘要）、看板（管道/风险单/部门网格）、问数、预警（筛选）、报告（内部 vs 对客脱敏对比）、HITL（SLA 倒计时实时刷新 1h56m→1h34m…） |
| D2 | 数据诚信专项 | ✅ 通过 | 演示数据处全部有"演示数据"标注（看板 3 处 / 预警 / 报告 / 离线待办 / 离线 HITL）；**零** lorem/TODO/XXX/测试文本/placeholder；语义全部真实（A型号/SMT-1/锡膏偏移/千住金属/深南电路/BATCH-A-1008/PRT-01/OVN-02/华为终端）；body emoji 数 = 0 |
| D3 | 视觉标准 | ✅ 通过 | `--primary: #185FA5` 企业蓝；卡片化布局；中文排版无乱码；**1024px 六屏零横向溢出**（含 provenance 展开态，SQL 块最大 633px 自换行） |
| D4 | 控制台双通道 | ✅ 基本通过 | 真实模式：0 错误 / 0 警告 / 0 pageerror。演示模式：0 JS 错误 / 0 pageerror / 0 警告；有 2 条**网络级** `ERR_CONNECTION_REFUSED`（见问题 2，非产品缺陷） |
| E | 已知限制 4 项裁判 | ✅ 全部接受 | 见第 6 节 |

---

## 2. A · 启动与冒烟（独立复现）

- **工程师"服务器仍在 8004 运行"的声明不成立**：QA 首测 `http://127.0.0.1:8004/api/v2/health` 连接拒绝（与主理人实测一致）。8004 实际空闲，QA 按文档命令 `uv run python -m uvicorn mfg_agents.v2.demo.api:app --port 8004` 自行启动成功。
- `/demo-ui/` → 200（3994 bytes，`text/html`）；`/demo-ui` → 307（StaticFiles html=True 行为正确）。
- `uv run pytest tests/ -q` → **280 passed, 2 skipped, 0 failed in 20.57s** —— api.py 增量对 V1/V2 零破坏的客观证据成立。
- 新端点行为：pending 返回 4 单真实待办（含 `context.card`、`history`、`sla_due_at`）；approve/reject 不存在 task_id → 404 + 中文 detail；events/open 幂等预开正常。

## 3. B · 真实链路验收（核心）

### B.1 问数全链路（截图 `real-chat-linestop.png`）

页面点击快捷问题"A型号在SMT-1停线，何时复线？"，EventSource 插桩计数：

- **SSE 收到 33 事件；节点流水 11 个**：`business → confirm_stop → [equipment ∥ quality ∥ supply_chain]（三列并行）→ fanin → fa → impact_wf → hitl_restart → hitl_eta → report`（插桩 `data-node` 序列与后端 `agent_start` 节点一一对应）。
- 结果卡徽标：`真实链路 / 路由 L0 / 置信度 1 / degraded 降级 / run-b54ca88cb3d0`；评级 pill **B**；degraded 横幅"LLM 不可用，结论为规则/模板化输出…可人工复核"（本机 Ollama `127.0.0.1:11434` 离线已独立证实，degraded 归因真实）。
- provenance 折叠面板 **19 条**：5 条 SQL 原文（`equip_alarms`/`mfg_oee`/`mfg_stop_ongoing`/`quality_defect_dist`/`supply_batches_today`）+ 命中行数 + 表名 + **脱敏标记"敏感字段已按角色脱敏"**（supply_batches_today masked）+ 10 个节点耗时 chips。
- 对话流内 2 张 HITL 待确认卡（复线决策 / 对客 ETA）。

### B.2 HITL 真实状态变更（before/after + 库级佐证，截图 `real-hitl-center-after.png`）

| 动作 | DB before | DB after | UI 表现 | pending 队列 |
|---|---|---|---|---|
| 同意 wf-14e10c98b24c | `waiting_hitl` | **`running`** | 横幅"已同意 · 工单状态 running · 处理人 张启明"；toast；同意/驳回按钮移除；列表"已处理"徽标；详情历史 `approve waiting_hitl → running · 张启明 · 门户审批：同意` | 已移除 |
| 驳回 wf-0753cd9419fa | `waiting_hitl` | **`closed`** | 横幅"已驳回 · 工单状态 closed · 处理人 张启明"；历史 `reject waiting_hitl → closed · 张启明 · 门户审批：驳回` | 已移除 |

`platform_workflow_task` 表 history_json 直查确认处理人留痕与状态机迁移真实落库。

### B.3 人工兜底

"今天锡膏批次有问题" → `fallback=manual_ticket`，兜底卡含"**人工兜底是平台设计的一部分**——确定性流程之外的问题不臆造结论"。

### B.4 SSE 不丢事件（独立计数）

不经页面，直接用 SSE 客户端订阅同一 request_id：**后端实际推送 33 事件**（类型序列与页面插桩收到的 33 完全一致：10×agent_start + 20×tool/llm + 1×fan_in + 2×agent_end 尾… 合计 33）。页面零丢失。工程师"33 事件"声明属实。

## 4. C · 降级验收

- **排除假降级**：QA 主动 kill 后端进程后，curl `/api/v2/health` 连接拒绝、`/demo-ui/` 返回 502、netstat 无 LISTEN（仅 TIME_WAIT）——后端确实已停。
- file:// 直开 `index.html`：六屏完整渲染、无白屏；顶栏"**后端离线 · 演示模式**"、左下角环境"演示模式"、toast"后端离线：当前为演示模式"。
- 问数演示回放：11 节点逐节点动画、结果卡带"**演示回放**"徽标、HITL 卡带"演示回放 · 未真实创建工单"；**演示模式 SSE 连接数 = 0**（不连后端，纯本地回放）。
- HITL 演示审批：横幅 + toast 双明示"（演示模式，**未真实调用后端**）"。
- 各屏演示标注齐全；1024px 零溢出。

## 5. D · 渲染质量与视觉对标

- **六屏**：全部达标（截图 `real-dashboard/orders/chat-linestop/alerts/reports/hitl-center-after.png`、`demo-dashboard/chat-replay.png`）。工作台角色切换（总经理→PQE：王倩）实时生效；预警中心类型筛选（设备告警 → 2 条）正常；HITL SLA 倒计时 1s ticker 实时刷新。
- **数据诚信**：逐屏核查，"演示数据"标注无遗漏；无占位残渣；演示数据全部使用项目真实语义。
- **视觉**：#185FA5 企业蓝主色、卡片化、留白充足、中文排版无乱码、无 emoji 堆砌（body 0 emoji）、1024px 宽全屏无横向溢出（provenance 展开态同样通过）。
- **控制台**：真实模式全流程 0 错误 0 警告；演示模式 0 JS 错误 / 0 pageerror / 0 警告（网络级错误见问题 2）。

## 6. E · 已知限制裁判

| # | 工程师自报限制 | QA 实测 | 裁判 |
|---|---|---|---|
| ① | 仅注册 line_stop_v2，5 个快捷问题 4 个走人工兜底 | 实测 5 问：1 问命中 line_stop_v2，4 问 `fallback=manual_ticket`；UI 明示"人工兜底是平台设计的一部分" | **接受**（平台真实行为，非缺陷，UI 明示充分） |
| ② | degraded 模式横幅 | 结果卡 degraded 徽标 + 横幅均在，Ollama 离线归因证实 | **接受** |
| ③ | 看板/预警/报告演示数据标注 | 三屏页面级"演示数据"标注全部存在 | **接受** |
| ④ | file:// 直开时 API_PORT_CANDIDATES 端口探测限制 | 代码实测仅探测 8004（同源 + 127.0.0.1 + localhost）；文档 §5.4 已声明；http:// 方式不受限 | **接受**（文档化的已知限制，非缺陷） |

## 7. 问题与观察项

### 问题 1（P3 · 非阻塞观察项，不要求返工）— 工作台待办预览的已处理工单会被裁掉

- **位置**：`src/mfg_agents/v2/demo/ui/screens.js:279`（`tasks.slice(0, 4)`）+ `screens.js:103-118`（`mergeProcessedTasks` 将已处理工单追加在展示列表**末尾**）。
- **复现**：待办 ≥4 条时，在工作台待办中心点"同意"→ 审批真实成功（DB running、toast 弹出），但重渲染后该工单排在合并列表末尾、被 `slice(0,4)` 裁掉，**预览中看不到"已处理"徽标与处理横幅**；QA 首轮因此误将"按钮消失"记为通过，第二轮在 HITL 审批中心（全量列表）严格复测才确认按钮移除/横幅/留痕全部正常。
- **期望 vs 实际**：设计文档承诺的 before/after 保留在 HITL 审批中心兑现（已处理徽标 + 横幅 + 历史，实测正常）；工作台为"预览"语义（前 4 条），审批后工单移出预览属可接受行为，但**工程师验证记录中"门户点'同意'→ 处理横幅…"的观察在待办 ≥4 时不可复现**（会被裁掉），记录备查。
- **建议**（可选，不阻塞交付）：预览列表将已处理工单置顶，或预览底部加"另 N 单已处理"计数。

### 问题 2（声明不精确，非产品缺陷）— 演示模式控制台有 2 条网络级错误

- **现象**：后端停止 + file:// 直开时，console 采集到 2 条 `Failed to load resource: net::ERR_CONNECTION_REFUSED`。
- **归因**：与 `app.js:29` 的 2 个候选端口探测 fetch 一一对应——探测一个已停止的后端，浏览器必然记录网络级错误，**任何探测实现均无法避免**（除非改用非 fetch 机制）；JS 错误 / pageerror / 警告均为 0。
- **判定**：非产品缺陷，环境必然现象。但工程师说明文档 §4 声称"演示模式全流程 0 错误 0 警告"在此点上**不精确**，如实记录。

## 8. 路由判定与交付结论

- **路由判定：NoOne（无需返工）**。
  - 未发现必须修复的源码缺陷：真实链路（问数/SSE/HITL/兜底）全部接真后端并有库级佐证；降级模式完整且明示；渲染质量与数据诚信达标；测试套件 280 passed 零回归。
  - 问题 1 为 P3 非阻塞观察项（HITL 中心主路径已兑现设计承诺）；问题 2 为环境必然现象 + 文档声明修订建议。二者均记录在案，不阻塞交付。
- **交付结论：可以交付。** 前端展示应用达到验收标准，六屏可用、真实/演示双模式边界清晰、数据诚信无水分。

---

### 附：证据材料

- 截图（`.tmp-qa/`，未入库）：`real-dashboard.png`、`real-orders.png`、`real-chat-linestop.png`、`real-alerts.png`、`real-reports.png`、`real-hitl-center-after.png`、`demo-dashboard.png`、`demo-chat-replay.png`、`demo-chat-prov-1024.png`
- 结构化数据：`.tmp-qa/round1-real.json`、`round1c-hitl-strict.json`、`round1d-sse.json`、`round1e-demo.json`
- QA 脚本：`.tmp-qa/qa_*.py`（playwright，可复跑）
