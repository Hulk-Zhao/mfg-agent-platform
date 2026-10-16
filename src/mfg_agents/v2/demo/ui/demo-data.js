/**
 * 演示数据模块（demo-data.js）
 *
 * 说明：本文件仅承载"演示数据"——看板 / 预警 / 报告 / 后端离线时的问数回放。
 * 问数 / 事件流 / HITL 审批在真实后端可达时一律走真实链路（见 app.js / screens.js），
 * 演示数据必须与真实链路输出保持同构（字段结构一致），切换时 UI 零适配。
 * 语义素材取自项目真实场景：A型号 / SMT-1 / 锡膏偏移 / 千住金属 / 深南电路 /
 * 华为终端 / BATCH-A-1008 / wf- 工单号 / PRT-01 刮刀压力 / OVN-02 回流焊。
 */
(function () {
  "use strict";

  /** 角色（演示：切换只影响看板数据范围与待办列表） */
  const ROLES = [
    { id: "gm",  name: "总经理",   user: "张启明", dept: "management",    roles: ["coordinator"],
      greeting: "全局运营视图", desc: "汇总五大域组运行态势，聚焦订单履约风险与升级事项" },
    { id: "mfg", name: "制造经理", user: "李卫东", dept: "manufacturing", roles: ["manufacturing", "coordinator"],
      greeting: "制造运营视图", desc: "聚焦停线 / OEE / 设备告警 / 复线进度，制造域工单优先" },
    { id: "pqe", name: "PQE",     user: "王倩",   dept: "quality",       roles: ["quality"],
      greeting: "品质工程视图", desc: "聚焦 SPC / 不良分布 / 客诉 8D，品质域待办优先（敏感字段按角色脱敏）" },
    { id: "mc",  name: "物控",     user: "赵敏",   dept: "supply_chain",  roles: ["supply_chain"],
      greeting: "供应链视图", desc: "聚焦缺料 / 齐套率 / 到货批次，供应链域待办优先" },
    { id: "biz", name: "业务",     user: "陈昊",   dept: "sales",         roles: ["business"],
      greeting: "业务交付视图", desc: "聚焦对客 ETA / 订单临期 / 客诉响应，业务域待办优先" },
  ];

  /** 工作台 · 今日预警摘要（按角色取不同范围，演示数据） */
  const ALERT_SUMMARY = {
    gm:  { spc: 2, shortage: 3, order: 5, sla: 2 },
    mfg: { spc: 2, shortage: 1, order: 2, sla: 2 },
    pqe: { spc: 2, shortage: 0, order: 1, sla: 1 },
    mc:  { spc: 0, shortage: 3, order: 3, sla: 1 },
    biz: { spc: 0, shortage: 1, order: 5, sla: 1 },
  };

  /** 工作台 · 待办中心（后端不可达时的演示数据；标注"演示数据"） */
  const DEMO_TODOS = [
    {
      kind: "restart", icon: "restart",
      title: "复线决策确认：是否按 FA 建议 ETA 复线？",
      task_uid: "wf-1a06a9fa7625", approver: "制造现场负责人",
      meta: { line: "SMT-1", model: "A型号", rating: "B", eta: "2h" },
      sla_hours_left: 1.3,
    },
    {
      kind: "eta", icon: "eta",
      title: "对客 ETA 承诺确认：确认回复客户的预计恢复时间？",
      task_uid: "wf-25b850f751a4", approver: "业务经理",
      meta: { line: "SMT-1", model: "A型号", rating: "B", eta: "2h" },
      sla_hours_left: 1.6,
    },
    {
      kind: "impact", icon: "impact",
      title: "停线影响评估工单待反馈（计划 / 物控 / 业务）",
      task_uid: "wf-07853f21e584", approver: "计划 · 物控 · 业务",
      meta: { line: "SMT-1", model: "A型号", rating: "B", eta: "2h" },
      sla_hours_left: 0.4,
    },
  ];

  /** 订单履约看板（演示数据） */
  const ORDERS = {
    pipeline: [
      { stage: "已下单", count: 26, color: "" },
      { stage: "计划",   count: 21, color: "" },
      { stage: "采购",   count: 14, color: "" },
      { stage: "生产",   count: 33, color: "" },
      { stage: "质检",   count: 9,  color: "" },
      { stage: "发货",   count: 41, color: "" },
    ],
    risks: [
      { no: "SO-2026-10231", product: "A型号 × 8,000pcs", customer: "华为终端",
        level: "S", delay_days: 2, progress: 62,
        shortage: "锡膏 M7020（千住金属）到货延迟 36h", tags: ["延期风险", "缺料关联"] },
      { no: "SO-2026-10247", product: "A型号 × 12,000pcs", customer: "华为终端",
        level: "S", delay_days: 4, progress: 45,
        shortage: "PCB 批次 BATCH-A-1008（深南电路）客诉待 8D 闭环", tags: ["延期风险", "客诉关联"] },
      { no: "SO-2026-10186", product: "A型号 × 5,000pcs", customer: "比亚迪",
        level: "A", delay_days: 1, progress: 81,
        shortage: "无缺料；受 SMT-1 停线牵连，复线后追产", tags: ["延期风险"] },
      { no: "SO-2026-10205", product: "C型号 × 6,000pcs", customer: "联想",
        level: "B", delay_days: 3, progress: 38,
        shortage: "屏蔽罩齐套率 78%，采购在途", tags: ["延期风险"], tagsList: ["缺料关联", "齐套率不足"] },
    ],
    /** 16 部门 → 5 域（今日待办 / 已闭环 / 超时） */
    domains: [
      { name: "制造域", icon: "cpu", depts: [
        { name: "SMT车间",  todo: 5, done: 12, over: 1 },
        { name: "DIP车间",  todo: 3, done: 9,  over: 0 },
        { name: "总装车间", todo: 4, done: 11, over: 0 },
        { name: "生产计划", todo: 2, done: 7,  over: 1 },
      ]},
      { name: "品质域", icon: "flask", depts: [
        { name: "PQE",      todo: 4, done: 8,  over: 1 },
        { name: "IQC",      todo: 3, done: 10, over: 0 },
        { name: "OQC",      todo: 2, done: 9,  over: 0 },
        { name: "品质工程", todo: 3, done: 6,  over: 0 },
      ]},
      { name: "供应链域", icon: "truck", depts: [
        { name: "采购",     todo: 4, done: 7,  over: 1 },
        { name: "仓储物流", todo: 2, done: 8,  over: 0 },
        { name: "物控",     todo: 3, done: 6,  over: 0 },
      ]},
      { name: "设备域", icon: "gear", depts: [
        { name: "设备维护", todo: 3, done: 9,  over: 1 },
        { name: "厂务能源", todo: 1, done: 4,  over: 0 },
      ]},
      { name: "业务域", icon: "users", depts: [
        { name: "业务",     todo: 4, done: 12, over: 0 },
        { name: "客服",     todo: 2, done: 6,  over: 0 },
        { name: "订单管理", todo: 3, done: 10, over: 0 },
      ]},
    ],
  };

  /** 预警中心（演示数据；类型 / 等级 / 状态 / 联动部门） */
  const ALERTS = [
    { id: "AL-2026-1029", type: "SPC越限", level: "P1", status: "待处置",
      title: "SMT-1 线 A型号锡膏印刷 CPK=0.89，低于控制下限 1.33",
      detail: "近 3 批次体积偏移量呈上升趋势，与 PRT-01 刮刀压力异常（0.8MPa，基准 0.5）时间窗重合，判定为工艺参数漂移，建议停线复测并加严 IPQC。",
      tags: ["品质域 · PQE", "品质域 · 品质工程", "制造域 · SMT车间", "设备域 · 设备维护"],
      time: "18:12", owner: "PQE · 王倩" },
    { id: "AL-2026-1028", type: "缺料", level: "P1", status: "处置中",
      title: "锡膏 M7020（千住金属）到货延迟 36h，库存可维持 2 天",
      detail: "原定今晨到货批次受物流影响延迟；若 48h 内未到货，SMT-1 将面临断料停线。采购已启动备用供应商询价（需采购总监确认 >5 万加急单）。",
      tags: ["供应链域 · 采购", "供应链域 · 物控", "制造域 · SMT车间"],
      time: "17:40", owner: "物控 · 赵敏" },
    { id: "AL-2026-1027", type: "设备告警", level: "P2", status: "处置中",
      title: "PRT-01 印刷机刮刀压力 0.8MPa（基准 0.5MPa）",
      detail: "近 2 小时 3 条设备异常：刮刀压力偏高、钢网清洁周期 15min（基准 4h）、回流温区3 = 245℃（基准 235±5）。设备维护已现场处置。",
      tags: ["设备域 · 设备维护", "制造域 · SMT车间"],
      time: "16:58", owner: "设备 · 孙工" },
    { id: "AL-2026-1026", type: "订单临期", level: "P1", status: "待处置",
      title: "SO-2026-10231（华为终端 · A型号）剩余 2 天，按当前 OEE 32.1% 无法按期交付",
      detail: "停线直接损失产出约 1,200pcs；需评估追产班次或与客户协商分批交付。对客 ETA 承诺须业务经理确认后方可输出。",
      tags: ["业务域 · 业务", "供应链域 · 物控", "制造域 · 生产计划"],
      time: "16:20", owner: "业务 · 陈昊" },
    { id: "AL-2026-1025", type: "订单临期", level: "P2", status: "待处置",
      title: "SO-2026-10205（联想 · C型号）齐套率 78%，缺屏蔽罩",
      detail: "屏蔽罩采购在途，预计明日到货；若延迟将影响 3 天后投产。",
      tags: ["供应链域 · 采购", "业务域 · 订单管理"],
      time: "15:47", owner: "物控 · 赵敏" },
    { id: "AL-2026-1024", type: "设备告警", level: "P2", status: "已闭环",
      title: "OVN-02 回流焊温区3 = 245℃（基准 235±5℃）",
      detail: "温度校准完成，温区恢复至 234℃，闭环验证通过。",
      tags: ["设备域 · 设备维护", "品质域 · PQE"],
      time: "14:05", owner: "设备 · 孙工" },
    { id: "AL-2026-1023", type: "SPC越限", level: "P2", status: "已闭环",
      title: "DIP-2 线 B型号插件错件率 0.4%（阈值 0.2%）",
      detail: " feeder 站位核对 + 首件复核完成，错件率回落至 0.1%。",
      tags: ["品质域 · IQC", "制造域 · DIP车间"],
      time: "11:32", owner: "品质工程 · 周工" },
  ];

  /** 报告中心（结构复刻 CLI demo 真实输出；degraded 模板化对客报告） */
  const REPORTS = {
    internal: {
      title: "停线应急协同 · 内部简报",
      run_id: "run-a697e4d69c6f",
      route: { level: "L0", scenario: "line_stop_v2", domain: "manufacturing", confidence: 1.0 },
      brief: [
        { k: "停线确认", v: "True（SMT-1 存在进行中停线事件）" },
        { k: "生产", v: "SMT-1线A型号今日OEE=32.1%" },
        { k: "品质", v: "主导不良：锡膏偏移，共77pcs" },
        { k: "供应链", v: "今日1个批次到货，无来料异常" },
        { k: "设备", v: "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）" },
        { k: "FA评级", v: "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线" },
        { k: "仓储", v: "—" },
      ],
      workflow: "wf-07853f21e584 [running] 停线影响评估工单 | 受理: 计划,物控,业务 | SLA 2h | P1 通知",
      hitl: "wf-1a06a9fa7625 复线决策（制造现场负责人）/ wf-25b850f751a4 对客ETA（业务经理）",
    },
    customer: {
      title: "对客报告（脱敏）",
      // 脱敏词以 <b class="mask-hl" title="原文：停线"> 标注，鼠标悬停可见禁词原文
      html:
        '尊敬的客户：目前产线处于<b class="mask-hl" title="脱敏替换：停线 → 计划内暂停">计划内暂停</b>（<b class="mask-hl" title="脱敏替换：设备故障 → 产线例行校准">例行校准</b>），我们已启动<b class="mask-hl" title="脱敏替换：供应商来料不良 → 批次质量复核">批次质量复核</b>与<b class="mask-hl" title="脱敏替换：品质不良超标 → 工艺参数优化">工艺参数优化</b>。根据当前评估，预计约 <b>2 小时</b>恢复，届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。',
      plain:
        "尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核与工艺参数优化。根据当前评估，预计约 2 小时恢复，届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。",
    },
    /** 脱敏映射表（对应 node_registry.FORBIDDEN_WORDS） */
    mask_map: [
      { from: "设备故障", to: "产线例行校准" },
      { from: "供应商来料不良", to: "批次质量复核" },
      { from: "品质不良超标", to: "工艺参数优化" },
      { from: "停线", to: "计划内暂停" },
      { from: "根因", to: "主因分析" },
      { from: "维修", to: "维护校准" },
    ],
    versions: [
      { ver: "v2.0", time: "2026-10-16 18:27", actor: "系统生成", badge: "当前版本", badge_cls: "badge-green",
        note: "停线应急简报 + 对客脱敏报告（degraded 模板化 + 禁词表兜底）" },
      { ver: "v1.1", time: "2026-10-16 18:05", actor: "PQE · 王倩", badge: "已审定", badge_cls: "badge-blue",
        note: "脱敏词表修订：新增「根因 → 主因分析」映射，对客话术复核" },
      { ver: "v1.0", time: "2026-10-16 17:52", actor: "系统生成", badge: "历史版本", badge_cls: "badge-gray",
        note: "初版内部简报（三域并行取证 + FA 评级 B）" },
    ],
  };

  /** HITL 审批中心 · 演示待办（后端不可达时使用；SLA 倒计时按当前时间动态计算） */
  const DEMO_HITL = [
    { task_uid: "wf-1a06a9fa7625", kind: "restart", wf_id: "hitl_restart",
      title: "复线决策确认：是否按 FA 建议 ETA 复线？",
      approver_roles: ["制造现场负责人"], sla_hours_left: 1.3, priority: "P1",
      ctx: { run_id: "run-a697e4d69c6f", line_code: "SMT-1", product_model: "A型号",
             fa_rating: "B", eta_hours: 2, kind: "restart" } },
    { task_uid: "wf-25b850f751a4", kind: "eta", wf_id: "hitl_eta",
      title: "对客 ETA 承诺确认：确认回复客户的预计恢复时间？",
      approver_roles: ["业务经理"], sla_hours_left: 1.6, priority: "P1",
      ctx: { run_id: "run-a697e4d69c6f", line_code: "SMT-1", product_model: "A型号",
             fa_rating: "B", eta_hours: 2, kind: "eta" } },
    { task_uid: "wf-07853f21e584", kind: "impact", wf_id: "impact_assessment",
      title: "停线影响评估工单（计划 / 物控 / 业务反馈）",
      approver_roles: ["计划", "物控", "业务"], sla_hours_left: 0.4, priority: "P1",
      ctx: { run_id: "run-a697e4d69c6f", line_code: "SMT-1", product_model: "A型号",
             fa_rating: "B", eta_hours: 2, kind: "impact" } },
  ];

  /** 智能问数 · 后端离线时的演示回放（事件序列与结果均为真实链路的同构复刻） */
  const CHAT_DEMO = {
    query: "A型号在SMT-1停线，何时复线？",
    events: [
      { type: "agent_start", node: "business", label: "业务Agent · 意图识别" },
      { type: "tool_start", tool: "意图识别", detail: "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）" },
      { type: "tool_end", tool: "意图识别", detail: "识别场景：line_stop | 型号：A型号 | 线体：SMT-1" },
      { type: "agent_end", node: "business", label: "业务Agent · 意图识别", summary: "识别场景：line_stop | 型号：A型号 | 线体：SMT-1", status: "ok", elapsed_ms: 0.6 },
      { type: "agent_start", node: "confirm_stop", label: "制造Agent · 确认停线" },
      { type: "tool_start", tool: "SQL查询", detail: "查询SMT-1线A型号今日OEE与产出 + 进行中停线事件" },
      { type: "tool_end", tool: "SQL查询", detail: "返回2条产出，1条停线事件（原文）" },
      { type: "agent_end", node: "confirm_stop", label: "制造Agent · 确认停线", summary: "SMT-1线A型号今日OEE=32.1%", status: "ok", elapsed_ms: 2.5 },
      { type: "agent_start", node: "equipment", label: "设备Agent · 查EAM" },
      { type: "agent_start", node: "quality", label: "品质Agent · 查QMS" },
      { type: "agent_start", node: "supply_chain", label: "供应链Agent · 查SCM" },
      { type: "tool_start", tool: "SQL查询", detail: "查询SMT-1线A型号不良分布" },
      { type: "tool_start", tool: "SQL查询", detail: "查询SMT-1线设备异常" },
      { type: "tool_start", tool: "SQL查询", detail: "查询今日到货批次" },
      { type: "tool_end", tool: "SQL查询", detail: "返回2种不良" },
      { type: "tool_end", tool: "SQL查询", detail: "返回3条异常" },
      { type: "tool_end", tool: "SQL查询", detail: "返回1条批次（敏感字段已按角色脱敏: True）" },
      { type: "agent_end", node: "quality", label: "品质Agent · 查QMS", summary: "主导不良：锡膏偏移，共77pcs", status: "ok", elapsed_ms: 2.2 },
      { type: "agent_end", node: "equipment", label: "设备Agent · 查EAM", summary: "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）", status: "ok", elapsed_ms: 4.1 },
      { type: "agent_end", node: "supply_chain", label: "供应链Agent · 查SCM", summary: "今日1个批次到货，无来料异常", status: "ok", elapsed_ms: 3.6 },
      { type: "fan_in", node: "fanin", domains: ["equipment", "manufacturing", "orchestration", "quality", "supply_chain"], degraded: [], summary: "汇聚 5 域结果" },
      { type: "agent_start", node: "fa", label: "FA分析Agent · RAG推理" },
      { type: "tool_start", tool: "RAG检索", detail: "查询: SMT-1线 锡膏偏移 设备告警=E-201,E-205,E-301" },
      { type: "tool_end", tool: "RAG检索", detail: "匹配3条历史案例" },
      { type: "agent_end", node: "fa", label: "FA分析Agent · RAG推理", summary: "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论）", status: "ok", elapsed_ms: 536.2 },
      { type: "agent_start", node: "impact_wf", label: "工作流 · 停线影响评估工单" },
      { type: "agent_end", node: "impact_wf", label: "工作流 · 停线影响评估工单", summary: "工单 wf-07853f21e584 已创建（P1 通知 计划,物控,业务，SLA 2h）", status: "ok", elapsed_ms: 68.3 },
      { type: "agent_start", node: "hitl_restart", label: "HITL · 复线决策确认" },
      { type: "agent_end", node: "hitl_restart", label: "HITL · 复线决策确认", summary: "复线决策确认：是否按 FA 建议 ETA 复线？ → waiting_hitl（审批人：制造现场负责人）", status: "ok", elapsed_ms: 59.6 },
      { type: "agent_start", node: "hitl_eta", label: "HITL · 对客ETA承诺确认" },
      { type: "agent_end", node: "hitl_eta", label: "HITL · 对客ETA承诺确认", summary: "对客 ETA 承诺确认：确认回复客户的预计恢复时间？ → waiting_hitl（审批人：业务经理）", status: "ok", elapsed_ms: 32.6 },
      { type: "agent_start", node: "report", label: "报告Agent · 生成简报与对客报告" },
      { type: "agent_end", node: "report", label: "报告Agent · 生成简报与对客报告", summary: "对客报告已生成（degraded 模板化 + 禁词表兜底）", status: "ok", elapsed_ms: 0.4 },
    ],
    result: {
      query: "A型号在SMT-1停线，何时复线？",
      run_id: "run-a697e4d69c6f",
      route: { domain: "manufacturing", scenario_key: "line_stop_v2", confidence: 1.0,
               level: "L0", low_confidence: false,
               entities: { product_model: "A型号", line_code: "SMT-1", urgency: "normal", v1_scenario: "line_stop" },
               reason: "场景触发词命中: line_stop_v2" },
      scenario_key: "line_stop_v2",
      report: "尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核与工艺参数优化。根据当前评估，预计约 2 小时恢复，届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。",
      internal_brief: {
        "停线确认": true, "生产": "SMT-1线A型号今日OEE=32.1%",
        "品质": "主导不良：锡膏偏移，共77pcs",
        "供应链": "今日1个批次到货，无来料异常",
        "设备": "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）",
        "FA评级": "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论）",
        "仓储": "",
      },
      hitl: [
        { kind: "restart", prompt: "复线决策确认：是否按 FA 建议 ETA 复线？", status: "waiting_hitl",
          approver_roles: ["制造现场负责人"], step: "hitl_restart", task_uid: "wf-1a06a9fa7625",
          actions: [{ key: "confirm_restart", label: "确认复线方案" }, { key: "hold", label: "暂缓复线" }] },
        { kind: "eta", prompt: "对客 ETA 承诺确认：确认回复客户的预计恢复时间？", status: "waiting_hitl",
          approver_roles: ["业务经理"], step: "hitl_eta", task_uid: "wf-25b850f751a4",
          actions: [{ key: "confirm_eta", label: "确认对客ETA" }, { key: "revise_eta", label: "修改ETA" }] },
      ],
      workflow_tasks: [
        { ticket: { task_uid: "wf-07853f21e584", wf_id: "impact_assessment", status: "running",
                    title: "停线影响评估工单", assignees: ["计划", "物控", "业务"], priority: "P1",
                    sla_due_at: "" },
          notified: ["计划", "物控", "业务"], notice: { level: "P1", targets: ["计划", "物控", "业务"] } },
      ],
      degraded: true,
      degraded_reasons: ["FA 分析 LLM 不可用：模板化结构化结论", "报告生成 LLM 不可用：模板化 + 禁词表兜底"],
      timings: { business: 0.6, confirm_stop: 2.5, quality: 2.2, supply_chain: 2.3, equipment: 4.1,
                 fa: 536.2, impact_wf: 68.3, hitl_restart: 59.6, hitl_eta: 32.6, report: 0.4 },
      provenance: [
        { channel: "registered", query_name: "equip_alarms", domain: "equipment", tables: ["equipment_log"],
          row_count: 3, masked: false,
          sql: "SELECT equipment_id, alarm_code, param_name, param_value FROM equipment_log WHERE line_code = :line AND is_abnormal = 1" },
        { channel: "registered", query_name: "mfg_oee", domain: "manufacturing", tables: ["production_output"],
          row_count: 2, masked: false,
          sql: "SELECT record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min FROM production_output WHERE line_code = :line AND product_model = :model" },
        { channel: "registered", query_name: "mfg_stop_ongoing", domain: "manufacturing", tables: ["line_stop_event"],
          row_count: 1, masked: false,
          sql: "SELECT * FROM line_stop_event WHERE line_code = :line AND product_model = :model AND status = 'ongoing'" },
        { channel: "registered", query_name: "quality_defect_dist", domain: "quality", tables: ["quality_defect"],
          row_count: 2, masked: false,
          sql: "SELECT defect_type, SUM(defect_qty) AS total_qty FROM quality_defect WHERE line_code = :line AND product_model = :model GROUP BY defect_type" },
        { channel: "registered", query_name: "supply_batches_today", domain: "supply_chain", tables: ["material_batch"],
          row_count: 1, masked: true,
          sql: "SELECT batch_no, part_no, part_name, supplier, incoming_result FROM material_batch WHERE date(arrival_time) = '2026-10-15'" },
        { channel: "rag", domain: "fa", query: "SMT-1线 锡膏偏移 设备告警=E-201,E-205,E-301", case_count: 3 },
        { channel: "workflow", domain: "workflow", task_uid: "wf-07853f21e584", wf_id: "impact_assessment",
          assignees: ["计划", "物控", "业务"] },
        { channel: "hitl", domain: "hitl", task_uid: "wf-1a06a9fa7625", approver_roles: ["制造现场负责人"] },
        { channel: "report", domain: "report", degraded: true, masked_words: ["设备故障", "供应商来料不良", "品质不良超标", "停线", "根因", "维修"] },
        { channel: "node_timing", node: "business", elapsed_ms: 0.6, status: "ok" },
        { channel: "node_timing", node: "confirm_stop", elapsed_ms: 2.5, status: "ok" },
        { channel: "node_timing", node: "equipment", elapsed_ms: 4.1, status: "ok" },
        { channel: "node_timing", node: "quality", elapsed_ms: 2.2, status: "ok" },
        { channel: "node_timing", node: "supply_chain", elapsed_ms: 3.6, status: "ok" },
        { channel: "node_timing", node: "fa", elapsed_ms: 536.2, status: "ok" },
        { channel: "node_timing", node: "impact_wf", elapsed_ms: 68.3, status: "ok" },
        { channel: "node_timing", node: "hitl_restart", elapsed_ms: 59.6, status: "ok" },
        { channel: "node_timing", node: "hitl_eta", elapsed_ms: 32.6, status: "ok" },
        { channel: "node_timing", node: "report", elapsed_ms: 0.4, status: "ok" },
      ],
      elapsed_ms: 755, error: null, fallback: null,
    },
  };

  window.DEMO = {
    ROLES, ALERT_SUMMARY, DEMO_TODOS, ORDERS, ALERTS, REPORTS, DEMO_HITL, CHAT_DEMO,
  };
})();
