# 办公助手 Agent：竞品调研与植入评估

> 角色：资深产品经理 · 面向对象：产品/研发/行政/管理层
> 目标：调研企业办公 AI 助手竞品，评估在本平台（制造业多智能体协同平台）植入一个**独立 Agentic Agent**——「办公助手」，支持预订会议室、订餐等企业常用功能；当前以 demo 形态交付、后续可替换真实接口。

---

## 一、结论先行（TL;DR）

1. **值得做，但要做"组合位"而非"单点位"**：会议室预订、订餐、日程集成都已高度商品化（飞书/钉钉/M365/Envoy/美团等），自研单点无经济性；真正的空白是**跨系统办公事务编排 + 订单级人工在环（HITL）治理**，这正是本平台已有资产。
2. **差异化锚点 = 治理**：竞品写操作多靠"软治理"（钉钉会前 15 分钟提醒释放、Places 自动释放策略）或"付费权益"（M365 Copilot 房间预订需 Copilot 许可）；本平台可做「AI 自主办理 → 按金额/资源阈值路由审批 → 全量审计留痕」的硬治理，直接对标 Moveworks 的审批阈值 + 职责分离 + action allowlist。
3. **接口先行、demo 可换真**：办公能力抽象为 `OfficeBackend` 契约，demo 用内存实现，接飞书/钉钉/美餐/美团只替换后端实现，**Agent 与工具层零改动**——这是"demo 展示、后续可真实接入"的工程落法。
4. **开放协议是入场券**：MCP 已成事实标准（月下载量约 9700 万）、A2A 完成治理归口；办公工具统一注册进平台既有 MCP/A2A 同源工具层，一次接入、多端复用。

---

## 二、竞品调研

调研时效：2026-10 联网检索，核心来源时间落在 2025-11 ~ 2026-09。

### 2.1 协同办公平台自带 AI 助手

- **飞书（Feishu/Lark）**：「智能伙伴」为每人一个的对话式 Agent，官方明确支持"预约会议、总结纪要、跟进待办"，并可部署外部业务应用（[智能伙伴](https://www.feishu.cn/product/ai_companion)）。日历"会议室视图"查空闲、一键预定；鸿蒙版新增"会议室候补"（有空闲自动分配，[新华网 2026-04-21](http://www.news.cn/tech/20260421/5d9c052c6b41462c82c6bfa489e56398/c.html)）。定价：版本 ¥0/50/80/120 人/月，AI 企业版 ¥9.9 万/年（[飞书 AI](https://www.feishu.cn/product/feishuai)）。开放侧 Calendar 域已支持 MCP 工具"查日程/预订会议/预订会议室"（[飞书 MCP 文档](https://open.larkoffice.com/document/mcp_open_tools/feishu-cli-let-ai-actually-do-your-work-in-feishu.md)）。
- **钉钉**：AI 助理含钉钉助手、待办、智能日程（[钉钉 AI 助理](https://page.dingtalk.com/wow/dingtalk/default/dingtalk/dingtalkmagic)）。**自主写操作案例**：微博"小信"实现会议室预定、访客申请一站式，会前 15 分钟提醒签到或释放会议室（[钉钉案例](https://page.dingtalk.com/wow/dingtalk/default/dingtalk/yiuq3f5m0108)）。订餐侧为生态应用（"报餐宝"¥390/年起、酷应用互动卡片"一键预定"含超时/超份数校验，[报餐宝](https://appcenter.dingtalk.com/detailNew.html?goodsCode=DT_GOODS_881665393284018)）。
- **企业微信**：智能助理与可"调用工具"的智能机器人；WorkBuddy（腾讯 AI 工作台）已集成，可"结合权限范围内的消息/文档/会议理解任务，调用工作台开工"（[上观新闻](https://www.shobserver.cn/sgh/detail?id=4076035)）。
- **华为 WeLink**：小微助手 100+ 预置技能，一句话完成会议操作；企业数字员工 7×24 + 智能纪要（[小微助手](https://www.huaweicloud.com/product/welink/xiaowei.html)）。

### 2.2 国际生产力套件 AI

- **Microsoft 365 Copilot + Places**：Copilot Chat 已 GA"找可用时间→订房间→起草议程→发邀请"；Places 嵌入 Teams/Outlook，Teams Premium 含快速房间预订、**自动释放会议室策略**；**AI 房间预订需 Copilot 许可**（[Ignite 2025](https://news.microsoft.com/ignite-2025-book-of-news/)、[Places 概述](https://learn.microsoft.com/zh-cn/microsoft-365/places/places-overview)、[许可要求](https://learn.microsoft.com/en-us/microsoft-365/places/what-is-places/license-requirements)）。
- **Google Workspace Gemini**：支持 Calendar actions，对话创建日程并**先出草稿待编辑**再落库（[Actions 文档](https://docs.cloud.google.com/gemini/enterprise/docs/assistant-use-actions)）。
- **Zoom AI Companion 3.0 / Slack AI / Notion AI**：代理式检索与摘要为主，Slackbot 已支持 MCP Client 对 Slack Lists 增删改查（[Slack MCP Client](https://slack.com/blog/news/slackbots-mcp-client)）。

### 2.3 企业搜索 / 工作流 Agent 厂商

- **Moveworks**：跨系统多步执行，**治理最完整**——RBAC 最小权限、敏感决策路由人工、审批阈值、职责分离、action allowlist、全量审计、MCP 连接用员工自身账户（[治理指南](https://www.moveworks.com/us/en/resources/blog/risks-of-agentic-ai-enterprise-governance-mitigation-guide)）。
- **Glean**：企业知识图谱 + Agent Harness，无公开价目，市场报价约 $45–50/人/月（[定价](https://docs.glean.com/glean-core-suite-pricing)）。
- **Writer / Sierra / Dust**：分别面向 agentic 长程与 MCP tool use / 客服 CX / 内部工作流——均**非"内部行政+订餐+会议室"组合**。

### 2.4 垂直厂商

- **会议室预订**：Envoy（Reservations $5/资源/月；Workplace $3–7 活跃用户/月，[Envoy 定价](https://envoy.com/pricing)）、Robin、Condeco（Eptura）。
- **企业订餐/团餐**：美团企业版（770 万活跃商户、餐补额度自动生成、企业支付免报销、对公统一开票、可对接预算系统，[美团企业版](https://eps.meituan.com/)）、饿了么企业版（团餐、与钉钉企业用餐集成）、美餐（3 万余商户，[美餐](https://www.meican.com/noncafeteria)）。

### 2.5 工具调用标准

- **MCP**：2026-03 起 Claude、GPT-5.4、Gemini、Copilot、Bedrock 均默认兼容，月下载约 9700 万（[2026 综述](https://internet-pros.com/blog/model-context-protocol-mcp-ai-standard-2026/)）。
- **A2A**：由 Google 捐给 Linux Foundation，150+ 组织，与 MCP 同栈治理（[Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)）。

---

## 三、对比矩阵

| 产品 | 会议室预订 | 订餐/食堂 | 行政事务 | 审批 | 日历集成 | 自主动作治理 | 开放工具协议 |
|---|---|---|---|---|---|---|---|
| 飞书智能伙伴 | ✅（候补） | ⚠️弱 | ✅ | ✅ | ✅强 | ⚠️一般 | ✅ MCP |
| 钉钉 AI 助理 | ✅ | ✅生态 | ✅ | ✅ | ✅ | ⚠️提醒式 | ✅ 酷应用/API |
| 企业微信 + WorkBuddy | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ✅按权限范围 | ✅ OpenClaw |
| 华为 WeLink 小微 | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️一般 | ✅ 开放 API |
| M365 Copilot + Places | ✅ | ❌ | ✅ | ✅ | ✅强 | ✅自动释放策略 | ✅ MCP apps |
| Google Gemini | ✅草稿 | ❌ | ✅ | ⚠️ | ✅ | ✅草稿确认 | ✅ actions |
| Moveworks | ❌ | ❌ | ✅IT/HR | ✅ | ⚠️ | ✅强 | ✅ MCP |
| Envoy / Robin | ✅核心 | ❌ | ⚠️访客 | ❌ | ✅ | ⚠️自动释放 | ⚠️ |
| 美团 / 饿了么 / 美餐 | ❌ | ✅核心 | ⚠️ | ✅餐补审批 | ⚠️ | ⚠️ | ⚠️ |
| **本平台（拟植入）** | **✅** | **✅** | **✅** | **✅** | ⚠️待接入 | **✅订单级 HITL** | **✅ MCP + A2A** |

---

## 四、红海与蓝海

**红海（已做透，勿重复投入）**
1. 通用会议室预订与日历联动（飞书/钉钉/M365/Envoy/Robin 标准化）。
2. 会议纪要、待办、知识问答（IM 厂商标配）。
3. 企业订餐供给与结算（美团/饿了么/美餐的商户与对公开票壁垒高）。

**蓝海（本平台差异化机会）**
1. **订单级 HITL 写操作治理**：竞品多止于提醒/策略式自动动作；本平台可做"AI 自主下单/预订 → 按金额/资源阈值路由审批 → 审计留痕"，对标 Moveworks。
2. **制造业专属行政事务**：班车、工服/劳保领用、访客/门禁、车间加班餐/夜宵配餐——IM 通用助手覆盖薄弱。
3. **MCP/A2A 双栈开放**：把内部 ERP/SRM/食堂/会议室系统封装为 MCP Server，"一次接入、多端复用"。
4. **跨系统编排**：订餐 + 会议室 + 审批 + 报销一次对话完成，单一垂直厂商做不到。

---

## 五、植入评估

| 维度 | 评估 |
|---|---|
| 战略契合 | 高——办公助手是"入口型"能力，可把员工日常高频诉求收进平台，提升日活与粘性 |
| 技术可行性 | 高——复用平台既有 Agentic 框架、统一工具层、HITL 治理；新代码仅一个后端契约 + 一个 Agent + 工具注册 |
| 成本 | 低（demo）——内存后端 + 11 个工具；真实接入成本取决于对接哪家（飞书/钉钉 API、美餐/美团企业版 API） |
| 风险 | 中——**写操作（真实下单/预订）需严控**：本人归属、冲突拒绝、阈值审批、全量审计；隐私（用餐/位置数据）需最小化 |
| 差异化收益 | 高——"AI 自主办理 + 硬治理"是可直接售卖的治理资产（与平台既有主张一致） |
| 结论 | **建议植入**：先以独立 Agentic Agent + demo 后端上线演示，再按业务优先级逐个替换真实接口 |

---

## 六、产品设计

### 6.1 Agent 定位（独立成型）
办公助手是**单独一个 Agentic Agent**，与质量/制造/供应链/研发/财务/行政等域组智能体并列，但能力边界独立——它只做"办公"，不越界到生产/质量/财务业务数据。

- **意图**：会议室预订与查询、订餐（食堂/商务餐厅）、我的预订/订单管理、高影响办公事项人工确认。
- **边界**：只能**本人**名下预订/取消；时段冲突不得静默覆盖；不得代他人下单。
- **HITL 准则**：需授权资源（报告厅/培训室，`need_approval`）、商务客餐/宴请、订单金额 ≥ 500 元 → 必须停下发起人工确认工单；Agent **只发起、永不放行**。
- **防卡死**：ReAct 循环硬上限 8 步，超限输出"已完成部分 + 缺口"，绝不静默挂起。

### 6.2 工具清单（能力即工具，工具即边界）

| 工具 | 说明 | 类型 / 治理分级 |
|---|---|---|
| `list_rooms` | 按容量/设备筛选会议室（含位置、是否需授权） | 只读 · tier 0 |
| `find_available_rooms` | 查某时段可用会议室（自动规避冲突） | 只读 · tier 0 |
| `list_my_bookings` | 查我的会议室预订 | 只读 · tier 0 |
| `list_restaurants` | 列出园区餐厅（食堂/商务餐厅） | 只读 · tier 0 |
| `get_menu` | 查某餐厅菜单与价格 | 只读 · tier 0 |
| `list_my_orders` | 查我的订餐订单 | 只读 · tier 0 |
| `book_room` | 预订会议室（自动查冲突，返回预订号） | 写/留痕 · tier 2 |
| `cancel_booking` | 取消本人会议室预订 | 写/留痕 · tier 2 |
| `place_order` | 下单订餐（按菜单计价，返回订单号与金额） | 写/留痕 · tier 2 |
| `cancel_order` | 取消本人订餐订单 | 写/留痕 · tier 2 |
| `request_approval` | 发起人工确认（高影响事项） | 写/HITL |

统一工具层中另以 `office.*` 前缀注册上述 10 个能力（`request_approval` 复用平台既有 HITL 工单链），供 **MCP / A2A / function calling 同源调用**。

### 6.3 典型交互（示例）

- 员工："帮我订明天下午2点一个8人的会议室" → `find_available_rooms` → `book_room` → 返回"已预订 A栋202，预订号 BK00001"。
- 员工："中午订个餐，来两份红烧狮子头套餐" → `list_restaurants` → `get_menu` → `place_order` → 返回"订单号 OD00001，合计 30 元"。
- 员工："在报告厅订个全员大会场地" → 命中需授权资源 → `book_room` → `request_approval` → 返回"已提交待确认"，行政专员审批。

---

## 七、工程实现

### 7.1 代码结构（本次落地）

| 文件 | 职责 |
|---|---|
| `src/mfg_agents/v2/services/office.py` | `OfficeBackend` 契约 + `DemoOfficeBackend`（内存种子：5 间会议室、3 家餐厅、预订/订单记录；冲突拒绝、本人归属校验、`need_approval` 标记） |
| `src/mfg_agents/v2/agents/office.py` | 独立 `OfficeAssistantAgent`（ReAct 工具循环 + 高影响 HITL + 8 步防卡死）、`OFFICE_AGENT_CONFIG`、`OFFICE_TOOLS_META` |
| `src/mfg_agents/v2/core/router.py` | 办公意图快路径 `OFFICE_KEYWORDS` → `strategy="office"`（无需 LLM，离线可演示） |
| `src/mfg_agents/v2/core/orchestrator.py` | `V2Runtime.office` 注入；`_handle_office` 通道（LLM 不可用诚实降级转人工） |
| `src/mfg_agents/v2/mcp_tools/tools.py` | 统一工具层新增 `office` 域 10 个工具（tier 0/2，与 MCP/A2A 同源） |
| `src/mfg_agents/v2/demo/api.py` | `/api/v2/office/chat`、`/rooms`、`/restaurants`、`/my/{actor}`；`agents/configs` 纳入办公助手 |
| `tests/v2/test_office_agent.py` | 16 例：后端契约 / 路由 / 工具层 / Agent 循环 / HITL / 离线降级 |

### 7.2 如何替换真实接口（demo → 生产）

1. 实现 `OfficeBackend` 抽象方法（会议室、餐厅、下单、取消等），内部转发飞书/钉钉/美餐/美团企业版 API；
2. 注入替换：`set_office_backend(YourBackend())`，或在 `create_default_orchestrator()` 里把 `runtime.office` 换成真实适配器；
3. **Agent、工具层、MCP/A2A、前端全部零改动**——契约不变即无侵入。

### 7.3 演示脚本

```bash
# 启动本地服务
uv run python -m uvicorn mfg_agents.v2.demo.api:app --port 8004 --reload
# 办公助手对话（强制定向，返回工具调用留痕）
curl -X POST localhost:8004/api/v2/office/chat -H "Content-Type: application/json" \
  -d '{"query":"帮我订明天下午2点一个8人的会议室","user_ctx":{"user_id":"u1","name":"张三"}}'
# 会议室 / 餐厅目录；我的预订与订单
curl localhost:8004/api/v2/office/rooms
curl localhost:8004/api/v2/office/restaurants
curl localhost:8004/api/v2/office/my/张三
```

前台"帮助中心"已实时同步后端配置，新增的办公助手智能体与工具会自动出现（离线走内置兜底）。

---

## 八、风险与后续路线

**主要风险**
- 真实写操作副作用：必须坚持"本人归属 + 冲突拒绝 + 阈值审批 + 审计留痕"，幂等与撤销能力要在真实接入前补齐。
- 数据隐私：用餐、位置、参会人信息属敏感数据，需最小化采集与按岗位脱敏。
- 供应商锁定：优先走 MCP/A2A 标准封装，避免绑定单一 IM 厂商。

**后续路线（建议优先级）**
1. **P0（已完成）**：独立办公助手 Agent + demo 后端 + 统一工具层 + demo 端点 + 帮助中心展示。
2. **P1**：接真实会议室系统（飞书/钉钉日历 API），补冲突可见性与"会前自动释放"。
3. **P1**：接企业订餐（美餐/美团企业版），打通餐补额度与对公开票。
4. **P2**：扩展制造业专属行政事务（班车、工服/劳保领用、车间加班餐/夜宵）。
5. **P2**：与会签/报销场景联动，形成"订餐+会议室+审批+报销"一次对话闭环。

---

## 附：可直接引用的调研结论

1. 飞书智能伙伴已支持"预约会议"并落地会议室候补，证明办公 Agent 写操作已被主流接受。
2. 钉钉"小信"是最贴近需求的自主预订案例，采用"会前 15 分钟提醒签到/释放"的软治理，可作对标基线。
3. M365 Copilot 已 GA"对话订会议室"，但 AI 房间预订**需 Copilot 许可**，说明写操作被当作付费权益。
4. Google Gemini"先生成草稿、再由用户确认"是可借鉴的低风险写操作交互范式。
5. Moveworks 给出最完整的 Agent 治理清单（审批阈值、职责分离、action allowlist、审计、MCP 用员工账户），应作为 HITL 设计的直接参照。
6. 会议室预订垂直市场高度商品化（Envoy $3–7/人/月），自研无经济性，宜集成而非重造。
7. 订餐侧价值在供给与结算而非 Agent 能力，应通过 API 集成（美团 770 万商户 + 免报销对公开票）。
8. MCP 已成事实标准、A2A 完成治理归口，双协议接入是"进入生态"的必要条件而非加分项。
9. 国际 Agent 厂商无一家专注"内部行政+订餐+会议室"组合，**制造业内部办公 Agent 仍是空白位**。

> 资料缺口：Condeco（Eptura）与 Dust 本轮未获 2026 可引用一手来源，建议二次核实。
