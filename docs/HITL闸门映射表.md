# HITL 闸门映射表（R3 交付物）

> 回答一个问题：**平台上每一个「必须人工确认」的动作，分别属于哪类硬闸门、由谁发起、谁会签、谁终批，以及哪些缺口还没堵上。**
> 数据来源：`src/mfg_agents/v2/data/permissions.yaml`（谁能签，治理单一事实源）+ `src/mfg_agents/v2/data/gate_reasons.yaml`（为什么必须人确认，产品语义层）。本表是这两份配置的**人读投影**，不构成第二事实源；配置变更以 YAML 为准，本表同步勘误。
> 在线查看：帮助中心 →「治理槽位与闸门」（矩阵实时同步自权限目录）；审批中心每张卡片常驻展示对应槽位的「闸门理由 + 会签方及缺谁」。

## 一、三类硬闸门 ↔ 槽位映射

对标开沿（KAIYAN）参考图的三类硬闸门口径：

| 硬闸门 | 槽位 | 为什么必须人确认（摘要） |
|---|---|---|
| **对外消息** | `eta` 对客 ETA 承诺 | 说给客户的正式交期，超期即违约；承诺口径必须业务与产能双侧确认 |
| **对外消息** | `complaint_reply` 对客回复 | 客诉回复发出即代表公司立场，措辞与承诺必须与 8D 结论一致 |
| **对外消息** | `delivery_commit` 交期承诺 | 以 ATP/排产测算为据但测算有假设，正式承诺必须由有权解释假设的人确认 |
| **动钱** | **（无专属槽位，见 §三 缺口）** interim：`disposition` 来料处置的金额会签链 | 退货/换货/特采涉及供应商结算与质量放行，让步接收以质量风险换交付 |
| **改排产** | `restart` 复线决策 | 复线是质量风险与产能代价的权衡，影响整条产线在制订单 |
| **改排产** | `maintenance_window` 停机窗口 | 停机窗口直接改变排产与交付节奏，是产能与设备安全的交换 |
| **改排产** | `spc_action` 过程处置 | 加严检验/工程介入/计划内暂停改变过程与节拍，力度必须与过程能力证据匹配 |
| **改排产** | `order_review` 接单评审 | 接单即锁定产能与交期承诺，任一维度不匹配都不能接 |
| **质量关闭**（平台自设） | `d8_close` 8D 报告关闭 | 关闭即根因与纠正措施被正式采信，后续对客口径以此为准 |
| **报告审定**（平台自设） | `report_publish` 报告审定与派发 | 报告发布即代表平台与责任岗位的判定，数据缺口未闭合不许带病发布 |

## 二、槽位 × 三动词角色矩阵（permissions.yaml 投影）

动词口径：**propose** 发起该处置动作（提出决策）｜**cosign** 会签（多角色共同背书，缺一不可）｜**approve** 终批（会签齐备后放行）。

> 下表为 2026-10-22 时点投影；**权威数据以 `permissions.yaml` 与帮助中心实时矩阵为准**（本表明细仅作评审锚点，角色调整后以 YAML 为准回填本表）。

| 槽位 | propose | cosign | approve |
|---|---|---|---|
| `restart` 复线决策 | 现场/制造侧发起 | 质量 + 设备 | 现场负责人 / 质量总监 |
| `eta` 对客 ETA | 业务（sales） | 制造/计划（产能核验） | 业务（sales） |
| `disposition` 来料处置 | 质量侧（SQE/PQE） | 采购 + 业务 + 财务（金额链） | 质量总监 / QA 经理 |
| `d8_close` 8D 关闭 | 质量（QA） | 工程 + 业务 | 质量总监 |
| `complaint_reply` 对客回复 | 业务（sales） | QA（口径一致性） | 业务（sales） |
| `delivery_commit` 交期承诺 | 业务（sales） | 仓库（ATP 核验） | 业务（sales） |
| `maintenance_window` 停机窗口 | 设备工程 | 制造 + 计划 | 设备负责人 |
| `spc_action` 过程处置 | 质量（PQE） | 工程 + 制造 | 质量总监 |
| `order_review` 接单评审 | 业务（sales） | 工程 + 计划 | 业务经理（sales） |
| `report_publish` 报告审定 | 编制/责任岗位（sales） | 质量 + 业务 + 客服 | 总经理 / 质量总监 |

**铁律（构造期强制）**：Agent 身份角色不得持有任何 `*.approve`（`PermissionRegistry` 启动自检，违反即启动失败）——智能体只发起、不放行；终批必须由人类角色执行。

## 三、缺口登记（不许美化）

| # | 缺口 | 现状 | interim 口径 | 正式方案 |
|---|---|---|---|---|
| 1 | **动钱类无专属槽位**（加急采购 / 退供折价 / 备件紧急采购） | `GOVERNANCE_SLOTS` 10 槽按闸门语义命名，无资金承诺类 | 复用 `disposition` 的金额会签链（`fin` 角色在 cosign 链） | 新增 `hitl.spend_commit`（资金支出承诺）：permissions.yaml + GOVERNANCE_SLOTS + agentic enum/模板 + test_governance 4 处断言 + screens.js SLOT_LABEL，共 6 处约 15 行 + 1 个治理矩阵决策（propose=采购、cosign=财务+采购负责人、approve=总经理/财务经理），约 0.5 人日。**待治理角色拍板绑定矩阵后实施** |
| 2 | `agentic.py` 建单槽位 enum 曾缺 3 槽 | 已修（2026-10-22，R1 前置）：enum 动态对齐 GOVERNANCE_SLOTS | — | 已闭环 |
| 3 | `screens.js::SLOT_LABEL` 曾缺 3 项中文名 | 已修（2026-10-22）：补 spc_action / order_review / report_publish | — | 已闭环 |
| 4 | `permissions.yaml` 头部注释曾写「9 槽位」 | 已修（2026-10-22 勘误为 10） | — | 已闭环 |

## 四、验收对照（PRD R3）

| 验收标准 | 落点 |
|---|---|
| ① 审批中心每张卡片展示「为什么必须人确认」+「会签方及缺谁」 | `screens.js::gateReasonHtml` + `signoffGapHtml`，hitl 卡（`hitlChatCardHtml`）与工作台待办卡（`todoSummaryHtml`）常驻展示；闸门目录缺失时降级为槽位中文名，不空块 |
| ② permissions.yaml 槽位矩阵可在帮助中心查询 | `help-screen.js`「治理槽位与闸门」节：槽 × 三动词 × 持有角色（实时自 `/api/v2/governance/permissions`）+ 闸门类别 + why（实时自 `/api/v2/governance/gates`）+ 缺口说明 |
| ③ 三类硬闸门映射表成文 | 本文件 + 帮助中心节首的三类闸门对标说明 |
