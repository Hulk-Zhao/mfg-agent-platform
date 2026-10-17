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

  /** 智能问数 · 后端离线时的演示回放（**从真实运行输出采集生成**，非手写） */
  const CHAT_DEMOS = {
    "A型号在SMT-1停线，何时复线？": {
      "events": [
        {
          "type": "agent_start",
          "node": "business",
          "label": "业务Agent · 意图识别"
        },
        {
          "type": "tool_start",
          "tool": "意图识别",
          "detail": "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）"
        },
        {
          "type": "tool_end",
          "tool": "意图识别",
          "detail": "识别场景：line_stop | 型号：A型号 | 线体：SMT-1"
        },
        {
          "type": "agent_end",
          "node": "business",
          "label": "业务Agent · 意图识别",
          "summary": "识别场景：line_stop | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.6
        },
        {
          "type": "agent_start",
          "node": "confirm_stop",
          "label": "制造Agent · 确认停线"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询SMT-1线A型号今日OEE与产出 + 进行中停线事件"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2条产出，1条停线事件（原文）"
        },
        {
          "type": "agent_end",
          "node": "confirm_stop",
          "label": "制造Agent · 确认停线",
          "summary": "SMT-1线A型号今日OEE=32.1%",
          "status": "ok",
          "elapsed_ms": 475.9
        },
        {
          "type": "agent_start",
          "node": "equipment",
          "label": "设备Agent · 查EAM"
        },
        {
          "type": "agent_start",
          "node": "quality",
          "label": "品质Agent · 查QMS"
        },
        {
          "type": "agent_start",
          "node": "supply_chain",
          "label": "供应链Agent · 查SCM"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询SMT-1线A型号不良分布"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询SMT-1线设备异常"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询今日到货批次"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1条批次（敏感字段已按角色脱敏: True）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回3条异常"
        },
        {
          "type": "agent_end",
          "node": "supply_chain",
          "label": "供应链Agent · 查SCM",
          "summary": "今日1个批次到货，无来料异常",
          "status": "ok",
          "elapsed_ms": 2.8
        },
        {
          "type": "agent_end",
          "node": "equipment",
          "label": "设备Agent · 查EAM",
          "summary": "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）",
          "status": "ok",
          "elapsed_ms": 3.6
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2种不良"
        },
        {
          "type": "agent_end",
          "node": "quality",
          "label": "品质Agent · 查QMS",
          "summary": "主导不良：锡膏偏移，共77pcs",
          "status": "ok",
          "elapsed_ms": 3.7
        },
        {
          "type": "fan_in",
          "node": "fanin",
          "summary": "汇聚 5 域结果",
          "domains": [
            "equipment",
            "manufacturing",
            "orchestration",
            "quality",
            "supply_chain"
          ],
          "degraded": []
        },
        {
          "type": "agent_start",
          "node": "fa",
          "label": "FA分析Agent · RAG推理"
        },
        {
          "type": "tool_start",
          "tool": "RAG检索",
          "detail": "查询: SMT-1线 锡膏偏移 设备告警=E-201,E-205,E-301"
        },
        {
          "type": "tool_end",
          "tool": "RAG检索",
          "detail": "匹配3条历史案例"
        },
        {
          "type": "agent_end",
          "node": "fa",
          "label": "FA分析Agent · RAG推理",
          "summary": "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论）",
          "status": "ok",
          "elapsed_ms": 1978.1
        },
        {
          "type": "agent_start",
          "node": "impact_wf",
          "label": "impact_assessment"
        },
        {
          "type": "agent_end",
          "node": "impact_wf",
          "label": "impact_assessment",
          "summary": "工单 wf-7a97b342ab44 已创建（P1 通知 计划,物控,业务，SLA 2h）",
          "status": "ok",
          "elapsed_ms": 181.8
        },
        {
          "type": "agent_start",
          "node": "hitl_restart",
          "label": "hitl_restart"
        },
        {
          "type": "agent_end",
          "node": "hitl_restart",
          "label": "hitl_restart",
          "summary": "复线决策确认：是否按 FA 建议 ETA 复线？ → waiting_hitl（审批人：制造现场负责人）",
          "status": "ok",
          "elapsed_ms": 52.0
        },
        {
          "type": "agent_start",
          "node": "hitl_eta",
          "label": "hitl_eta"
        },
        {
          "type": "agent_end",
          "node": "hitl_eta",
          "label": "hitl_eta",
          "summary": "对客 ETA 承诺确认：确认回复客户的预计恢复时间？ → waiting_hitl（审批人：业务经理）",
          "status": "ok",
          "elapsed_ms": 31.9
        },
        {
          "type": "agent_start",
          "node": "report",
          "label": "报告Agent · 生成简报与对客报告"
        },
        {
          "type": "agent_end",
          "node": "report",
          "label": "报告Agent · 生成简报与对客报告",
          "summary": "对客报告已生成（degraded 模板化 + 禁词表兜底）",
          "status": "ok",
          "elapsed_ms": 0.5
        }
      ],
      "result": {
        "query": "A型号在SMT-1停线，何时复线？",
        "run_id": "run-9f3580dcf56b",
        "route": {
          "domain": "manufacturing",
          "scenario_key": "line_stop_v2",
          "confidence": 1.0,
          "level": "L0",
          "entities": {
            "product_model": "A型号",
            "line_code": "SMT-1",
            "urgency": "normal",
            "v1_scenario": "line_stop"
          },
          "low_confidence": false,
          "reason": "场景触发词命中: line_stop_v2"
        },
        "scenario_key": "line_stop_v2",
        "report": "尊敬的客户：目前产线处于计划内暂停（例行校准），我们已启动批次质量复核与工艺参数优化。根据当前评估，预计约 2 小时恢复，届时将第一时间同步发货安排。给贵司带来不便深表歉意，我们将持续跟进并及时通报进展。",
        "internal_brief": {
          "停线确认": true,
          "生产": "SMT-1线A型号今日OEE=32.1%",
          "品质": "主导不良：锡膏偏移，共77pcs",
          "供应链": "今日1个批次到货，无来料异常",
          "设备": "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）",
          "FA评级": "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：LLM 不可用，模板化结论）",
          "仓储": ""
        },
        "provenance": [
          {
            "channel": "registered",
            "query_name": "equip_alarms",
            "desc": "EAM：线体设备异常告警（等价 V1 EquipmentAgent）",
            "params": {
              "line": "SMT-1"
            },
            "tables": [
              "equipment_log"
            ],
            "row_count": 3,
            "elapsed_ms": 1.9,
            "sql": "SELECT equipment_id, alarm_code, param_name, param_value FROM equipment_log WHERE line_code = :line AND is_abnormal = 1",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "equipment"
          },
          {
            "channel": "rag",
            "query": "SMT-1线 锡膏偏移 设备告警=E-201,E-205,E-301",
            "case_count": 3,
            "domain": "fa"
          },
          {
            "channel": "hitl",
            "task_uid": "wf-a9f25e7a7692",
            "approver_roles": [
              "业务经理"
            ],
            "domain": "hitl"
          },
          {
            "channel": "registered",
            "query_name": "mfg_oee",
            "desc": "MES：指定线体/型号当日 OEE 与产出（等价 V1 ManufacturingAgent）",
            "params": {
              "line": "SMT-1",
              "model": "A型号"
            },
            "tables": [
              "production_output"
            ],
            "row_count": 2,
            "elapsed_ms": 474.4,
            "sql": "SELECT record_date, line_code, product_model, planned_qty, actual_qty, oee, stop_duration_min FROM production_output WHERE line_code = :line AND product_model = :model AND record_date = '2026-10-15'",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "manufacturing"
          },
          {
            "channel": "registered",
            "query_name": "mfg_stop_ongoing",
            "desc": "MES：进行中的停线事件（等价 V1 ManufacturingAgent）",
            "params": {
              "line": "SMT-1",
              "model": "A型号"
            },
            "tables": [
              "line_stop_event"
            ],
            "row_count": 1,
            "elapsed_ms": 1.2,
            "sql": "SELECT * FROM line_stop_event WHERE line_code = :line AND product_model = :model AND status = 'ongoing'",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "manufacturing"
          },
          {
            "channel": "registered",
            "query_name": "quality_defect_dist",
            "desc": "QMS：不良分布聚合（等价 V1 QualityAgent）",
            "params": {
              "line": "SMT-1",
              "model": "A型号"
            },
            "tables": [
              "quality_defect"
            ],
            "row_count": 2,
            "elapsed_ms": 3.3,
            "sql": "SELECT defect_type, SUM(defect_qty) AS total_qty FROM quality_defect WHERE line_code = :line AND product_model = :model GROUP BY defect_type",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "quality"
          },
          {
            "channel": "report",
            "degraded": true,
            "masked_words": [
              "设备故障",
              "供应商来料不良",
              "品质不良超标",
              "停线",
              "根因",
              "维修"
            ],
            "domain": "report"
          },
          {
            "channel": "registered",
            "query_name": "supply_batches_today",
            "desc": "SCM：今日到货来料批次（等价 V1 SupplyChainAgent）",
            "params": {},
            "tables": [
              "material_batch"
            ],
            "row_count": 1,
            "elapsed_ms": 1.2,
            "sql": "SELECT batch_no, part_no, part_name, supplier, incoming_result FROM material_batch WHERE date(arrival_time) = '2026-10-15'",
            "masked": true,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "supply_chain"
          },
          {
            "channel": "workflow",
            "task_uid": "wf-7a97b342ab44",
            "wf_id": "impact_assessment",
            "assignees": [
              "计划",
              "物控",
              "业务"
            ],
            "domain": "workflow"
          },
          {
            "channel": "node_timing",
            "node": "business",
            "elapsed_ms": 0.6,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "confirm_stop",
            "elapsed_ms": 475.9,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "equipment",
            "elapsed_ms": 3.6,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "fa",
            "elapsed_ms": 1978.1,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "hitl_eta",
            "elapsed_ms": 31.9,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "hitl_restart",
            "elapsed_ms": 52.0,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "impact_wf",
            "elapsed_ms": 181.8,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "quality",
            "elapsed_ms": 3.7,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "report",
            "elapsed_ms": 0.5,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "supply_chain",
            "elapsed_ms": 2.8,
            "status": "ok"
          }
        ],
        "hitl": [
          {
            "step": "hitl_restart",
            "kind": "restart",
            "prompt": "复线决策确认：是否按 FA 建议 ETA 复线？",
            "status": "waiting_hitl",
            "approver_roles": [
              "制造现场负责人"
            ],
            "actions": [
              {
                "key": "confirm_restart",
                "label": "确认复线方案"
              },
              {
                "key": "hold",
                "label": "暂缓复线"
              }
            ],
            "task_uid": "wf-a5251c74daf4",
            "card": {
              "title": "[待确认] 复线决策确认：是否按 FA 建议 ETA 复线？",
              "body": "复线决策确认：是否按 FA 建议 ETA 复线？\n线体: SMT-1 | 型号: A型号\nFA评级: B | 预估复线: 2h\n审批人: 制造现场负责人",
              "actions": [
                {
                  "key": "confirm_restart",
                  "label": "确认复线方案"
                },
                {
                  "key": "hold",
                  "label": "暂缓复线"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-a5251c74daf4",
                "approver_roles": [
                  "制造现场负责人"
                ]
              }
            ]
          },
          {
            "step": "hitl_eta",
            "kind": "eta",
            "prompt": "对客 ETA 承诺确认：确认回复客户的预计恢复时间？",
            "status": "waiting_hitl",
            "approver_roles": [
              "业务经理"
            ],
            "actions": [
              {
                "key": "confirm_eta",
                "label": "确认对客ETA"
              },
              {
                "key": "revise_eta",
                "label": "修改ETA"
              }
            ],
            "task_uid": "wf-a9f25e7a7692",
            "card": {
              "title": "[待确认] 对客 ETA 承诺确认：确认回复客户的预计恢复时间？",
              "body": "对客 ETA 承诺确认：确认回复客户的预计恢复时间？\n线体: SMT-1 | 型号: A型号\nFA评级: B | 预估复线: 2h\n审批人: 业务经理",
              "actions": [
                {
                  "key": "confirm_eta",
                  "label": "确认对客ETA"
                },
                {
                  "key": "revise_eta",
                  "label": "修改ETA"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-a9f25e7a7692",
                "approver_roles": [
                  "业务经理"
                ]
              }
            ]
          }
        ],
        "workflow_tasks": [
          {
            "ticket": {
              "task_uid": "wf-7a97b342ab44",
              "wf_id": "impact_assessment",
              "title": "停线影响评估工单",
              "status": "running",
              "priority": "P1",
              "assignees": [
                "计划",
                "物控",
                "业务"
              ],
              "context": {
                "run_id": "run-9f3580dcf56b",
                "line_code": "SMT-1",
                "product_model": "A型号",
                "confirmed_stop": true,
                "fa_rating": "B",
                "eta_hours": 2,
                "root_cause": "设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差"
              },
              "hitl_prompt": "",
              "history": [
                {
                  "ts": "2026-10-17T01:23:42.333633+00:00",
                  "action": "create",
                  "actor": "system",
                  "comment": "",
                  "from": "pending",
                  "to": "pending",
                  "reason": "创建工单"
                },
                {
                  "ts": "2026-10-17T01:23:42.480352+00:00",
                  "action": "start",
                  "actor": "orchestrator",
                  "comment": "影响评估启动",
                  "from": "pending",
                  "to": "running",
                  "reason": "影响评估启动"
                }
              ],
              "created_at": "2026-10-17T01:23:42.333580+00:00",
              "updated_at": "2026-10-17T01:23:42.480337+00:00",
              "sla_due_at": "2026-10-17T03:23:42.333580+00:00"
            },
            "notified": [
              "计划",
              "物控",
              "业务"
            ],
            "notice": {
              "level": "P1",
              "targets": [
                "计划",
                "物控",
                "业务"
              ],
              "title": "停线影响评估（SMT-1）",
              "body": "停线影响评估工单\n线体: SMT-1 | 型号: A型号\nFA评级: B | 预估复线: 2h\n请评估排产/物料/对客影响（SLA 2h，超时自动升级）"
            },
            "provenance": [
              {
                "channel": "workflow",
                "task_uid": "wf-7a97b342ab44",
                "wf_id": "impact_assessment",
                "assignees": [
                  "计划",
                  "物控",
                  "业务"
                ]
              }
            ]
          }
        ],
        "timings": {
          "business": 0.6,
          "confirm_stop": 475.9,
          "supply_chain": 2.8,
          "equipment": 3.6,
          "quality": 3.7,
          "fa": 1978.1,
          "impact_wf": 181.8,
          "hitl_restart": 52.0,
          "hitl_eta": 31.9,
          "report": 0.5
        },
        "degraded": true,
        "degraded_reasons": [
          "FA 分析 LLM 不可用：模板化结构化结论",
          "报告生成 LLM 不可用：模板化 + 禁词表兜底"
        ],
        "fallback": null,
        "elapsed_ms": 2768.6,
        "error": null
      }
    },
    "今天锡膏批次有问题": {
      "events": [
        {
          "type": "agent_start",
          "node": "intake",
          "label": "业务Agent · 意图识别"
        },
        {
          "type": "tool_start",
          "tool": "意图识别",
          "detail": "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）"
        },
        {
          "type": "tool_end",
          "tool": "意图识别",
          "detail": "识别场景：incoming_quality | 型号：A型号 | 线体：SMT-1"
        },
        {
          "type": "agent_end",
          "node": "intake",
          "label": "业务Agent · 意图识别",
          "summary": "识别场景：incoming_quality | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.4
        },
        {
          "type": "agent_start",
          "node": "sqe_assess",
          "label": "SQE · 来料不合格判定"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 iqc_batches_recent（判定）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行"
        },
        {
          "type": "agent_end",
          "node": "sqe_assess",
          "label": "SQE · 来料不合格判定",
          "summary": "2 个不合格批次：MAT-SP-1014（无铅锡膏SAC305，供应商 供应商***，COA http://coa.example.com/sp1014.pdf）；处置建议：退货、换货、挑选",
          "status": "degraded",
          "elapsed_ms": 2.4
        },
        {
          "type": "agent_start",
          "node": "material_control",
          "label": "物控 · 库存/替代"
        },
        {
          "type": "agent_start",
          "node": "planning",
          "label": "计划 · 排产影响"
        },
        {
          "type": "agent_start",
          "node": "procurement",
          "label": "采购 · 供应商跟催"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 plan_impact_by_material（注册查询）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询库存可用量（on_hand - reserved）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 supplier_followup（注册查询）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行（原文）"
        },
        {
          "type": "agent_end",
          "node": "planning",
          "label": "计划 · 排产影响",
          "summary": "受影响排产 2 条：计划10000/实际6800",
          "status": "ok",
          "elapsed_ms": 4.3
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询型号库存与订单量（缺口推演）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行库存，3行缺口推演"
        },
        {
          "type": "agent_end",
          "node": "material_control",
          "label": "物控 · 库存/替代",
          "summary": "可用库存 2 个型号仓；其中 0 个仓可用量为 0",
          "status": "ok",
          "elapsed_ms": 7.0
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回3行（脱敏）"
        },
        {
          "type": "agent_end",
          "node": "procurement",
          "label": "采购 · 供应商跟催",
          "summary": "供应商跟催与评级：供应商***(IC-100) 2批/不合格1；供应商***(SP-001) 2批/不合格1",
          "status": "ok",
          "elapsed_ms": 7.5
        },
        {
          "type": "fan_in",
          "node": "fanin",
          "summary": "汇聚 3 域结果（降级: quality）",
          "domains": [
            "orchestration",
            "quality",
            "supply_chain"
          ],
          "degraded": [
            "quality"
          ]
        },
        {
          "type": "agent_start",
          "node": "scar_wf",
          "label": "scar_flow"
        },
        {
          "type": "tool_start",
          "tool": "工作流",
          "detail": "创建工单：来料异常处置/SCAR 工单（P1，SLA 4h）"
        },
        {
          "type": "tool_end",
          "tool": "工作流",
          "detail": "工单 wf-633adb734008 已创建（P1 通知 采购,计划,物控，SLA 4h）"
        },
        {
          "type": "agent_end",
          "node": "scar_wf",
          "label": "scar_flow",
          "summary": "工单 wf-633adb734008 已创建（P1 通知 采购,计划,物控，SLA 4h）",
          "status": "ok",
          "elapsed_ms": 85.1
        },
        {
          "type": "agent_start",
          "node": "hitl_disposition",
          "label": "hitl_disposition"
        },
        {
          "type": "agent_end",
          "node": "hitl_disposition",
          "label": "hitl_disposition",
          "summary": "退换货/特采决策确认 → waiting_hitl（审批人：SQE、PQE、生产）",
          "status": "ok",
          "elapsed_ms": 42.8
        },
        {
          "type": "agent_start",
          "node": "report_incoming",
          "label": "报告Agent · 来料异常简报"
        },
        {
          "type": "agent_end",
          "node": "report_incoming",
          "label": "报告Agent · 来料异常简报",
          "summary": "内部简报已生成（6 项）",
          "status": "ok",
          "elapsed_ms": 0.5
        }
      ],
      "result": {
        "query": "今天锡膏批次有问题",
        "run_id": "run-c9321360892b",
        "route": {
          "domain": "quality",
          "scenario_key": "incoming_quality_v2",
          "confidence": 1.0,
          "level": "L0",
          "entities": {
            "product_model": "A型号",
            "line_code": "SMT-1",
            "urgency": "normal",
            "v1_scenario": "incoming_quality"
          },
          "low_confidence": false,
          "reason": "场景触发词命中: incoming_quality_v2"
        },
        "scenario_key": "incoming_quality_v2",
        "report": "",
        "internal_brief": {
          "来料不合格判定": "2 个不合格批次：MAT-SP-1014（无铅锡膏SAC305，供应商 供应商***，COA http://coa.example.com/sp1014.pdf）；处置建议：退货、换货、挑选",
          "缺陷等级": "A",
          "处置建议": "退货；换货；挑选",
          "采购跟催": "供应商跟催与评级：供应商***(IC-100) 2批/不合格1；供应商***(SP-001) 2批/不合格1",
          "排产影响": "受影响排产 2 条：计划10000/实际6800",
          "物料评估": "可用库存 2 个型号仓；其中 0 个仓可用量为 0"
        },
        "provenance": [
          {
            "channel": "hitl",
            "task_uid": "wf-8405f96d4725",
            "approver_roles": [
              "SQE",
              "PQE",
              "生产"
            ],
            "domain": "hitl"
          },
          {
            "channel": "registered",
            "query_name": "iqc_batches_recent",
            "desc": "QMS：最近不合格来料批次（含 COA，供 SQE 判定）",
            "params": {},
            "tables": [
              "material_batch"
            ],
            "row_count": 2,
            "elapsed_ms": 1.9,
            "sql": "SELECT batch_no, part_no, part_name, supplier, incoming_result, coa_url, arrival_time FROM material_batch WHERE incoming_result = 'fail' ORDER BY arrival_time DESC LIMIT 5",
            "masked": true,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "quality"
          },
          {
            "channel": "report",
            "scenario": "incoming_quality_v2",
            "degraded": true,
            "masked_words": [
              "设备故障",
              "供应商来料不良",
              "品质不良超标",
              "停线",
              "根因",
              "维修"
            ],
            "domain": "report"
          },
          {
            "channel": "registered",
            "query_name": "supplier_followup",
            "desc": "SRM：供应商到货/不合格统计（跟催与评级）",
            "params": {},
            "tables": [
              "material_batch"
            ],
            "row_count": 3,
            "elapsed_ms": 5.6,
            "sql": "SELECT supplier, part_no, part_name, COUNT(*) AS batch_cnt, SUM(CASE WHEN incoming_result='fail' THEN 1 ELSE 0 END) AS fail_cnt FROM material_batch GROUP BY supplier, part_no",
            "masked": true,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "supply_chain"
          },
          {
            "channel": "workflow",
            "task_uid": "wf-633adb734008",
            "wf_id": "scar_flow",
            "assignees": [
              "采购",
              "计划",
              "物控"
            ],
            "domain": "workflow"
          },
          {
            "channel": "node_timing",
            "node": "hitl_disposition",
            "elapsed_ms": 42.8,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "intake",
            "elapsed_ms": 0.4,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "material_control",
            "elapsed_ms": 7.0,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "planning",
            "elapsed_ms": 4.3,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "procurement",
            "elapsed_ms": 7.5,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "report_incoming",
            "elapsed_ms": 0.5,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "scar_wf",
            "elapsed_ms": 85.1,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "sqe_assess",
            "elapsed_ms": 2.4,
            "status": "degraded"
          }
        ],
        "hitl": [
          {
            "step": "hitl_disposition",
            "kind": "disposition",
            "prompt": "退换货/特采决策确认",
            "status": "waiting_hitl",
            "approver_roles": [
              "SQE",
              "PQE",
              "生产"
            ],
            "actions": [
              {
                "key": "return_goods",
                "label": "退货"
              },
              {
                "key": "replace",
                "label": "换货"
              },
              {
                "key": "concession",
                "label": "特采（三方会签）"
              },
              {
                "key": "sort",
                "label": "挑选"
              }
            ],
            "task_uid": "wf-8405f96d4725",
            "card": {
              "title": "[待确认] 退换货/特采决策确认",
              "body": "退换货/特采决策确认\n线体: SMT-1 | 型号: A型号\nFA评级: ? | 预估复线: ?h\n审批人: SQE、PQE、生产",
              "actions": [
                {
                  "key": "return_goods",
                  "label": "退货"
                },
                {
                  "key": "replace",
                  "label": "换货"
                },
                {
                  "key": "concession",
                  "label": "特采（三方会签）"
                },
                {
                  "key": "sort",
                  "label": "挑选"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-8405f96d4725",
                "approver_roles": [
                  "SQE",
                  "PQE",
                  "生产"
                ]
              }
            ]
          }
        ],
        "workflow_tasks": [
          {
            "ticket": {
              "task_uid": "wf-633adb734008",
              "wf_id": "scar_flow",
              "title": "来料异常处置/SCAR 工单",
              "status": "running",
              "priority": "P1",
              "assignees": [
                "采购",
                "计划",
                "物控"
              ],
              "context": {
                "run_id": "run-c9321360892b",
                "line_code": "SMT-1",
                "product_model": "A型号",
                "step": "scar_wf",
                "iqc_rejected": true,
                "defect_grade": "A"
              },
              "hitl_prompt": "",
              "history": [
                {
                  "ts": "2026-10-17T01:23:42.633533+00:00",
                  "action": "create",
                  "actor": "system",
                  "comment": "",
                  "from": "pending",
                  "to": "pending",
                  "reason": "创建工单"
                },
                {
                  "ts": "2026-10-17T01:23:42.686128+00:00",
                  "action": "start",
                  "actor": "orchestrator",
                  "comment": "来料异常处置/SCAR 工单启动",
                  "from": "pending",
                  "to": "running",
                  "reason": "来料异常处置/SCAR 工单启动"
                }
              ],
              "created_at": "2026-10-17T01:23:42.633505+00:00",
              "updated_at": "2026-10-17T01:23:42.686112+00:00",
              "sla_due_at": "2026-10-17T05:23:42.633505+00:00"
            },
            "notified": [
              "采购",
              "计划",
              "物控"
            ],
            "notice": {
              "level": "P1",
              "targets": [
                "采购",
                "计划",
                "物控"
              ],
              "title": "来料异常处置/SCAR 工单",
              "body": "来料异常处置/SCAR 工单\n请相关角色处理（SLA 4h，超时自动升级）"
            },
            "provenance": [
              {
                "channel": "workflow",
                "task_uid": "wf-633adb734008",
                "wf_id": "scar_flow",
                "assignees": [
                  "采购",
                  "计划",
                  "物控"
                ]
              }
            ]
          }
        ],
        "timings": {
          "intake": 0.4,
          "sqe_assess": 2.4,
          "planning": 4.3,
          "material_control": 7.0,
          "procurement": 7.5,
          "scar_wf": 85.1,
          "hitl_disposition": 42.8,
          "report_incoming": 0.5
        },
        "degraded": true,
        "degraded_reasons": [
          "报告生成 LLM 不可用：模板化 + 禁词表兜底"
        ],
        "fallback": null,
        "elapsed_ms": 160.9,
        "error": null
      }
    },
    "华为投诉BATCH-A-1008": {
      "events": [
        {
          "type": "agent_start",
          "node": "intake",
          "label": "业务Agent · 意图识别"
        },
        {
          "type": "tool_start",
          "tool": "意图识别",
          "detail": "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）"
        },
        {
          "type": "tool_end",
          "tool": "意图识别",
          "detail": "识别场景：customer_complaint | 型号：A型号 | 线体：SMT-1"
        },
        {
          "type": "agent_end",
          "node": "intake",
          "label": "业务Agent · 意图识别",
          "summary": "识别场景：customer_complaint | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.3
        },
        {
          "type": "agent_start",
          "node": "qa_lead",
          "label": "QA · 8D 牵头"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询未闭环客诉单（8D D2 问题界定）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1条未闭环客诉"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询批次BATCH-A-1008不良分布（8D D4）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1类不良"
        },
        {
          "type": "agent_end",
          "node": "qa_lead",
          "label": "QA · 8D 牵头",
          "summary": "客诉 1 单（未闭环）；8D 骨架已合成，D2=华为终端",
          "status": "degraded",
          "elapsed_ms": 2.8
        },
        {
          "type": "agent_start",
          "node": "business_sales",
          "label": "业务 · 对客口径"
        },
        {
          "type": "agent_start",
          "node": "engineering",
          "label": "工程 · 设计根因"
        },
        {
          "type": "agent_start",
          "node": "pqe_process",
          "label": "PQE · 过程分析"
        },
        {
          "type": "agent_start",
          "node": "sqe_supplier",
          "label": "SQE · 供应商侧"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 shipment_status（注册查询）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询批次BATCH-A-1008过程不良分布"
        },
        {
          "type": "tool_start",
          "tool": "规则模板",
          "detail": "无 LLM/无数据源 → 模板化结论（不编造）"
        },
        {
          "type": "tool_end",
          "tool": "规则模板",
          "detail": "设计根因：无 BOM/ECN/图纸数据源，未编造结论"
        },
        {
          "type": "agent_end",
          "node": "engineering",
          "label": "工程 · 设计根因",
          "summary": "设计根因：数据缺口（无 BOM/ECN 数据源）",
          "status": "degraded",
          "elapsed_ms": 2.2
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "按批号 BATCH-A-1008 查供应商"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1类不良"
        },
        {
          "type": "agent_end",
          "node": "pqe_process",
          "label": "PQE · 过程分析",
          "summary": "过程不良分布：1类，主导 贴片偏移（46pcs，合计46pcs）",
          "status": "ok",
          "elapsed_ms": 3.5
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1行"
        },
        {
          "type": "agent_end",
          "node": "sqe_supplier",
          "label": "SQE · 供应商侧",
          "summary": "批次供应商：供应商***（来料检验：合格）",
          "status": "ok",
          "elapsed_ms": 4.5
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行（原文）"
        },
        {
          "type": "agent_end",
          "node": "business_sales",
          "label": "业务 · 对客口径",
          "summary": "对客订单/交期 2 单：SO-20261015-01 待发货 ETA 2026-10-17 10:00:00；SO-20261010-03 待发货 ETA 2026-10-14 10:00:00",
          "status": "ok",
          "elapsed_ms": 5.5
        },
        {
          "type": "fan_in",
          "node": "fanin",
          "summary": "汇聚 4 域结果（降级: rd_engineering）",
          "domains": [
            "orchestration",
            "quality",
            "rd_engineering",
            "supply_chain"
          ],
          "degraded": [
            "rd_engineering"
          ]
        },
        {
          "type": "agent_start",
          "node": "capa_flow",
          "label": "complaint_8d_flow"
        },
        {
          "type": "tool_start",
          "tool": "工作流",
          "detail": "创建工单：客诉 8D 整改工单（P1，SLA 72h）"
        },
        {
          "type": "tool_end",
          "tool": "工作流",
          "detail": "工单 wf-c4bd412f60e1 已创建（P1 通知 QA,PQE,SQE,工程,业务，SLA 72h）"
        },
        {
          "type": "agent_end",
          "node": "capa_flow",
          "label": "complaint_8d_flow",
          "summary": "工单 wf-c4bd412f60e1 已创建（P1 通知 QA,PQE,SQE,工程,业务，SLA 72h）",
          "status": "ok",
          "elapsed_ms": 62.9
        },
        {
          "type": "agent_start",
          "node": "hitl_d8_close",
          "label": "hitl_d8_close"
        },
        {
          "type": "agent_end",
          "node": "hitl_d8_close",
          "label": "hitl_d8_close",
          "summary": "8D 报告关闭确认 → waiting_hitl（审批人：质量总监、QA经理）",
          "status": "ok",
          "elapsed_ms": 39.5
        },
        {
          "type": "agent_start",
          "node": "hitl_complaint_reply",
          "label": "hitl_complaint_reply"
        },
        {
          "type": "agent_end",
          "node": "hitl_complaint_reply",
          "label": "hitl_complaint_reply",
          "summary": "对客回复确认（脱敏后） → waiting_hitl（审批人：业务经理）",
          "status": "ok",
          "elapsed_ms": 33.4
        },
        {
          "type": "agent_start",
          "node": "report_complaint",
          "label": "报告Agent · 8D/对客报告"
        },
        {
          "type": "agent_end",
          "node": "report_complaint",
          "label": "报告Agent · 8D/对客报告",
          "summary": "内部简报已生成（6 项）；含数据缺口；对客报告（degraded 模板化）",
          "status": "ok",
          "elapsed_ms": 0.3
        }
      ],
      "result": {
        "query": "华为投诉BATCH-A-1008",
        "run_id": "run-bcd6bee7b5d0",
        "route": {
          "domain": "quality",
          "scenario_key": "customer_complaint_v2",
          "confidence": 1.0,
          "level": "L0",
          "entities": {
            "product_model": "A型号",
            "line_code": "SMT-1",
            "urgency": "high",
            "v1_scenario": "customer_complaint"
          },
          "low_confidence": false,
          "reason": "场景触发词命中: customer_complaint_v2"
        },
        "scenario_key": "customer_complaint_v2",
        "report": "尊敬的客户：关于贵司反馈的批次质量问题，我们已立案并启动 8D 专项整改，对在库/在途批次实施围堵与复检。当前主因分析进行中，整改措施与关闭时间将以正式 8D 报告同步。感谢理解与支持。",
        "internal_brief": {
          "客诉摘要": "客诉 1 单（未闭环）；8D 骨架已合成，D2=华为终端",
          "8D报告": "D1_team=质量牵头（QA）+ PQE + SQE + 工程 + 业务（客户接口）；D2_problem=华为终端 反馈：贴片元件偏移导致功能失效，不良率约2.3%（型号 A型号，批次 BATCH-A-1008）；D3_containment=在库/在途该批次冻结，已发货批次通知客户加严检验；D4_root_cause=过程不良以「贴片偏移」为主（46pcs），疑似贴装/工艺偏移；D5_permanent=工艺参数复核 + 首件全检 + 供应商来料加严（如需）；D6_validation=连续 3 批 CPK 达标且无同类不良后解除围堵；D7_prevent=更新 SOP/FMEA，纳入过程监控与供应商评级；D8_closure=待质量总监/QA 经理 HITL 确认关闭",
          "过程分析": "过程不良分布：1类，主导 贴片偏移（46pcs，合计46pcs）",
          "供应商侧": "批次供应商：供应商***（来料检验：合格）",
          "设计根因": "无 BOM/ECN/图纸数据源，未做设计根因推断",
          "对客口径": "对客订单/交期 2 单：SO-20261015-01 待发货 ETA 2026-10-17 10:00:00；SO-20261010-03 待发货 ETA 2026-10-14 10:00:00",
          "数据缺口": "设计根因：无 BOM/ECN/图纸数据源，未编造结论"
        },
        "provenance": [
          {
            "channel": "hitl",
            "task_uid": "wf-777eda1737fb",
            "approver_roles": [
              "业务经理"
            ],
            "domain": "hitl"
          },
          {
            "channel": "registered",
            "query_name": "batch_supplier_by_no",
            "desc": "QMS：按批号查供应商与检验结论",
            "params": {
              "batch_no": "BATCH-A-1008"
            },
            "tables": [
              "material_batch"
            ],
            "row_count": 1,
            "elapsed_ms": 2.2,
            "sql": "SELECT batch_no, part_no, part_name, supplier, incoming_result FROM material_batch WHERE batch_no = :batch_no",
            "masked": true,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "quality"
          },
          {
            "channel": "template",
            "domain": "rd_engineering",
            "degraded": true,
            "note": "规则模板生成（无 LLM/无数据源）"
          },
          {
            "channel": "data_gap",
            "domain": "rd_engineering",
            "note": "设计根因：无 BOM/ECN/图纸数据源，未编造结论"
          },
          {
            "channel": "report",
            "scenario": "customer_complaint_v2",
            "degraded": true,
            "masked_words": [
              "设备故障",
              "供应商来料不良",
              "品质不良超标",
              "停线",
              "根因",
              "维修"
            ],
            "domain": "report"
          },
          {
            "channel": "data_gap",
            "domain": "report",
            "note": "设计根因：无 BOM/ECN/图纸数据源，未编造结论"
          },
          {
            "channel": "registered",
            "query_name": "shipment_status",
            "desc": "TMS：型号发货单状态与 ETA（等价 V1 WarehouseAgent）",
            "params": {
              "model": "A型号"
            },
            "tables": [
              "shipment"
            ],
            "row_count": 2,
            "elapsed_ms": 4.8,
            "sql": "SELECT order_no, product_model, qty, ship_status, eta FROM shipment WHERE product_model = :model",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "supply_chain"
          },
          {
            "channel": "workflow",
            "task_uid": "wf-c4bd412f60e1",
            "wf_id": "complaint_8d_flow",
            "assignees": [
              "QA",
              "PQE",
              "SQE",
              "工程",
              "业务"
            ],
            "domain": "workflow"
          },
          {
            "channel": "node_timing",
            "node": "business_sales",
            "elapsed_ms": 5.5,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "capa_flow",
            "elapsed_ms": 62.9,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "engineering",
            "elapsed_ms": 2.2,
            "status": "degraded"
          },
          {
            "channel": "node_timing",
            "node": "hitl_complaint_reply",
            "elapsed_ms": 33.4,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "hitl_d8_close",
            "elapsed_ms": 39.5,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "intake",
            "elapsed_ms": 0.3,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "pqe_process",
            "elapsed_ms": 3.5,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "qa_lead",
            "elapsed_ms": 2.8,
            "status": "degraded"
          },
          {
            "channel": "node_timing",
            "node": "report_complaint",
            "elapsed_ms": 0.3,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "sqe_supplier",
            "elapsed_ms": 4.5,
            "status": "ok"
          }
        ],
        "hitl": [
          {
            "step": "hitl_d8_close",
            "kind": "d8_close",
            "prompt": "8D 报告关闭确认",
            "status": "waiting_hitl",
            "approver_roles": [
              "质量总监",
              "QA经理"
            ],
            "actions": [
              {
                "key": "close_8d",
                "label": "确认关闭"
              },
              {
                "key": "reopen",
                "label": "退回补充"
              }
            ],
            "task_uid": "wf-012d7aea6786",
            "card": {
              "title": "[待确认] 8D 报告关闭确认",
              "body": "8D 报告关闭确认\n线体: SMT-1 | 型号: A型号\nFA评级: ? | 预估复线: ?h\n审批人: 质量总监、QA经理",
              "actions": [
                {
                  "key": "close_8d",
                  "label": "确认关闭"
                },
                {
                  "key": "reopen",
                  "label": "退回补充"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-012d7aea6786",
                "approver_roles": [
                  "质量总监",
                  "QA经理"
                ]
              }
            ]
          },
          {
            "step": "hitl_complaint_reply",
            "kind": "complaint_reply",
            "prompt": "对客回复确认（脱敏后）",
            "status": "waiting_hitl",
            "approver_roles": [
              "业务经理"
            ],
            "actions": [
              {
                "key": "confirm_reply",
                "label": "确认回复"
              },
              {
                "key": "revise_reply",
                "label": "修改回复"
              }
            ],
            "task_uid": "wf-777eda1737fb",
            "card": {
              "title": "[待确认] 对客回复确认（脱敏后）",
              "body": "对客回复确认（脱敏后）\n线体: SMT-1 | 型号: A型号\nFA评级: ? | 预估复线: ?h\n审批人: 业务经理",
              "actions": [
                {
                  "key": "confirm_reply",
                  "label": "确认回复"
                },
                {
                  "key": "revise_reply",
                  "label": "修改回复"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-777eda1737fb",
                "approver_roles": [
                  "业务经理"
                ]
              }
            ]
          }
        ],
        "workflow_tasks": [
          {
            "ticket": {
              "task_uid": "wf-c4bd412f60e1",
              "wf_id": "complaint_8d_flow",
              "title": "客诉 8D 整改工单",
              "status": "running",
              "priority": "P1",
              "assignees": [
                "QA",
                "PQE",
                "SQE",
                "工程",
                "业务"
              ],
              "context": {
                "run_id": "run-bcd6bee7b5d0",
                "line_code": "SMT-1",
                "product_model": "A型号",
                "step": "capa_flow",
                "complaint_found": true
              },
              "hitl_prompt": "",
              "history": [
                {
                  "ts": "2026-10-17T01:23:42.791426+00:00",
                  "action": "create",
                  "actor": "system",
                  "comment": "",
                  "from": "pending",
                  "to": "pending",
                  "reason": "创建工单"
                },
                {
                  "ts": "2026-10-17T01:23:42.821712+00:00",
                  "action": "start",
                  "actor": "orchestrator",
                  "comment": "客诉 8D 整改工单启动",
                  "from": "pending",
                  "to": "running",
                  "reason": "客诉 8D 整改工单启动"
                }
              ],
              "created_at": "2026-10-17T01:23:42.791397+00:00",
              "updated_at": "2026-10-17T01:23:42.821706+00:00",
              "sla_due_at": "2026-10-20T01:23:42.791397+00:00"
            },
            "notified": [
              "QA",
              "PQE",
              "SQE",
              "工程",
              "业务"
            ],
            "notice": {
              "level": "P1",
              "targets": [
                "QA",
                "PQE",
                "SQE",
                "工程",
                "业务"
              ],
              "title": "客诉 8D 整改工单",
              "body": "客诉 8D 整改工单\n请相关角色处理（SLA 72h，超时自动升级）"
            },
            "provenance": [
              {
                "channel": "workflow",
                "task_uid": "wf-c4bd412f60e1",
                "wf_id": "complaint_8d_flow",
                "assignees": [
                  "QA",
                  "PQE",
                  "SQE",
                  "工程",
                  "业务"
                ]
              }
            ]
          }
        ],
        "timings": {
          "intake": 0.3,
          "qa_lead": 2.8,
          "engineering": 2.2,
          "pqe_process": 3.5,
          "sqe_supplier": 4.5,
          "business_sales": 5.5,
          "capa_flow": 62.9,
          "hitl_d8_close": 39.5,
          "hitl_complaint_reply": 33.4,
          "report_complaint": 0.3
        },
        "degraded": true,
        "degraded_reasons": [
          "报告生成 LLM 不可用：模板化 + 禁词表兜底"
        ],
        "fallback": null,
        "elapsed_ms": 166.0,
        "error": null
      }
    },
    "A型号能发多少货": {
      "events": [
        {
          "type": "agent_start",
          "node": "intake",
          "label": "业务Agent · 意图识别"
        },
        {
          "type": "tool_start",
          "tool": "意图识别",
          "detail": "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）"
        },
        {
          "type": "tool_end",
          "tool": "意图识别",
          "detail": "识别场景：delivery_risk | 型号：A型号 | 线体：SMT-1"
        },
        {
          "type": "agent_end",
          "node": "intake",
          "label": "业务Agent · 意图识别",
          "summary": "识别场景：delivery_risk | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.2
        },
        {
          "type": "agent_start",
          "node": "business_order",
          "label": "业务 · 订单/交期"
        },
        {
          "type": "agent_start",
          "node": "logistics",
          "label": "物流 · 在途"
        },
        {
          "type": "agent_start",
          "node": "manufacturing_output",
          "label": "制造 · 产出"
        },
        {
          "type": "agent_start",
          "node": "material_control",
          "label": "物控 · 缺口"
        },
        {
          "type": "agent_start",
          "node": "planning",
          "label": "计划 · 排产"
        },
        {
          "type": "agent_start",
          "node": "warehouse_atp",
          "label": "仓库 · ATP"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 shipment_status（注册查询）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询库存可用量（on_hand - reserved）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 orders_by_model（注册查询）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 plan_impact_by_material（注册查询）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询型号库存与订单量（缺口推演）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 output_by_model（注册查询）"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 A型号 可用库存与发货状态"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行（原文）"
        },
        {
          "type": "agent_end",
          "node": "business_order",
          "label": "业务 · 订单/交期",
          "summary": "订单 2 单，合计 3500pcs",
          "status": "ok",
          "elapsed_ms": 7.7
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行（原文）"
        },
        {
          "type": "agent_end",
          "node": "logistics",
          "label": "物流 · 在途",
          "summary": "发货 2 单，在途 0 单",
          "status": "ok",
          "elapsed_ms": 8.2
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1行（原文）"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行（原文）"
        },
        {
          "type": "agent_end",
          "node": "manufacturing_output",
          "label": "制造 · 产出",
          "summary": "产出达成：A型号 累计 6800pcs，平均OEE 59.2",
          "status": "ok",
          "elapsed_ms": 9.0
        },
        {
          "type": "agent_end",
          "node": "planning",
          "label": "计划 · 排产",
          "summary": "受影响排产 2 条：计划10000/实际6800",
          "status": "ok",
          "elapsed_ms": 8.9
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2行库存，3行缺口推演"
        },
        {
          "type": "agent_end",
          "node": "material_control",
          "label": "物控 · 缺口",
          "summary": "可用库存 2 个型号仓；其中 0 个仓可用量为 0",
          "status": "ok",
          "elapsed_ms": 9.8
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回1行库存，2行发货"
        },
        {
          "type": "agent_end",
          "node": "warehouse_atp",
          "label": "仓库 · ATP",
          "summary": "A型号 可用库存合计 400pcs（1个仓）",
          "status": "ok",
          "elapsed_ms": 10.2
        },
        {
          "type": "fan_in",
          "node": "fanin",
          "summary": "汇聚 3 域结果",
          "domains": [
            "manufacturing",
            "orchestration",
            "supply_chain"
          ],
          "degraded": []
        },
        {
          "type": "agent_start",
          "node": "atp_analysis",
          "label": "ATP 分析 · 可承诺量"
        },
        {
          "type": "tool_end",
          "tool": "代码计算",
          "detail": "ATP=400，缺口=3100"
        },
        {
          "type": "agent_end",
          "node": "atp_analysis",
          "label": "ATP 分析 · 可承诺量",
          "summary": "A型号 ATP=400pcs，订单需求=3500pcs，缺口=3100pcs，风险订单 2 单",
          "status": "ok",
          "elapsed_ms": 0.2
        },
        {
          "type": "agent_start",
          "node": "delivery_risk_wf",
          "label": "delivery_risk_flow"
        },
        {
          "type": "tool_start",
          "tool": "工作流",
          "detail": "创建工单：出货风险协同工单（P1，SLA 8h）"
        },
        {
          "type": "tool_end",
          "tool": "工作流",
          "detail": "工单 wf-6b2f45b6e1b2 已创建（P1 通知 计划,物控,业务,物流，SLA 8h）"
        },
        {
          "type": "agent_end",
          "node": "delivery_risk_wf",
          "label": "delivery_risk_flow",
          "summary": "工单 wf-6b2f45b6e1b2 已创建（P1 通知 计划,物控,业务,物流，SLA 8h）",
          "status": "ok",
          "elapsed_ms": 78.8
        },
        {
          "type": "agent_start",
          "node": "hitl_delivery_commit",
          "label": "hitl_delivery_commit"
        },
        {
          "type": "agent_end",
          "node": "hitl_delivery_commit",
          "label": "hitl_delivery_commit",
          "summary": "对客交期承诺确认 → waiting_hitl（审批人：业务经理）",
          "status": "ok",
          "elapsed_ms": 35.6
        },
        {
          "type": "agent_start",
          "node": "report_delivery",
          "label": "报告Agent · 出货风险简报"
        },
        {
          "type": "agent_end",
          "node": "report_delivery",
          "label": "报告Agent · 出货风险简报",
          "summary": "内部简报已生成（7 项）；对客报告（degraded 模板化）",
          "status": "ok",
          "elapsed_ms": 0.4
        }
      ],
      "result": {
        "query": "A型号能发多少货",
        "run_id": "run-99f31d7a4709",
        "route": {
          "domain": "supply_chain",
          "scenario_key": "delivery_status_v2",
          "confidence": 1.0,
          "level": "L0",
          "entities": {
            "product_model": "A型号",
            "line_code": "SMT-1",
            "urgency": "normal",
            "v1_scenario": "delivery_risk"
          },
          "low_confidence": false,
          "reason": "场景触发词命中: delivery_status_v2"
        },
        "scenario_key": "delivery_status_v2",
        "report": "尊敬的客户：经核实，贵司订单当前受产能与物料因素影响，存在交付风险。我方已启动跨部门协同（计划/物控/物流），并在确认后第一时间同步可承诺交期。敬请谅解，感谢支持。",
        "internal_brief": {
          "ATP承诺分析": "A型号 ATP=400pcs，订单需求=3500pcs，缺口=3100pcs，风险订单 2 单",
          "风险订单": "order_no=SO-20261015-01；qty=2000；eta=2026-10-17 10:00:00；ship_status=待发货；order_no=SO-20261010-03；qty=1500；eta=2026-10-14 10:00:00；ship_status=待发货",
          "订单": "订单 2 单，合计 3500pcs",
          "排产": "受影响排产 2 条：计划10000/实际6800",
          "库存评估": "可用库存 2 个型号仓；其中 0 个仓可用量为 0",
          "产出": "产出达成：A型号 累计 6800pcs，平均OEE 59.2",
          "物流在途": "发货 2 单，在途 0 单"
        },
        "provenance": [
          {
            "channel": "hitl",
            "task_uid": "wf-b18739545dd8",
            "approver_roles": [
              "业务经理"
            ],
            "domain": "hitl"
          },
          {
            "channel": "registered",
            "query_name": "output_by_model",
            "desc": "MES：型号产出达成与平均 OEE",
            "params": {
              "model": "A型号"
            },
            "tables": [
              "production_output"
            ],
            "row_count": 1,
            "elapsed_ms": 3.5,
            "sql": "SELECT product_model, SUM(actual_qty) AS actual_qty, AVG(oee) AS oee FROM production_output WHERE product_model = :model GROUP BY product_model",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "manufacturing"
          },
          {
            "channel": "report",
            "scenario": "delivery_status_v2",
            "degraded": true,
            "masked_words": [
              "设备故障",
              "供应商来料不良",
              "品质不良超标",
              "停线",
              "根因",
              "维修"
            ],
            "domain": "report"
          },
          {
            "channel": "compute",
            "formula": "on_hand - reserved",
            "available_qty": 400.0,
            "demand_qty": 3500.0,
            "gap_qty": 3100.0,
            "source": "warehouse_available + orders_by_model",
            "domain": "supply_chain"
          },
          {
            "channel": "workflow",
            "task_uid": "wf-6b2f45b6e1b2",
            "wf_id": "delivery_risk_flow",
            "assignees": [
              "计划",
              "物控",
              "业务",
              "物流"
            ],
            "domain": "workflow"
          },
          {
            "channel": "node_timing",
            "node": "atp_analysis",
            "elapsed_ms": 0.2,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "business_order",
            "elapsed_ms": 7.7,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "delivery_risk_wf",
            "elapsed_ms": 78.8,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "hitl_delivery_commit",
            "elapsed_ms": 35.6,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "intake",
            "elapsed_ms": 0.2,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "logistics",
            "elapsed_ms": 8.2,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "manufacturing_output",
            "elapsed_ms": 9.0,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "material_control",
            "elapsed_ms": 9.8,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "planning",
            "elapsed_ms": 8.9,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "report_delivery",
            "elapsed_ms": 0.4,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "warehouse_atp",
            "elapsed_ms": 10.2,
            "status": "ok"
          }
        ],
        "hitl": [
          {
            "step": "hitl_delivery_commit",
            "kind": "delivery_commit",
            "prompt": "对客交期承诺确认",
            "status": "waiting_hitl",
            "approver_roles": [
              "业务经理"
            ],
            "actions": [
              {
                "key": "confirm_commit",
                "label": "确认交期"
              },
              {
                "key": "revise_commit",
                "label": "调整交期"
              }
            ],
            "task_uid": "wf-b18739545dd8",
            "card": {
              "title": "[待确认] 对客交期承诺确认",
              "body": "对客交期承诺确认\n线体: SMT-1 | 型号: A型号\nFA评级: ? | 预估复线: ?h\n审批人: 业务经理",
              "actions": [
                {
                  "key": "confirm_commit",
                  "label": "确认交期"
                },
                {
                  "key": "revise_commit",
                  "label": "调整交期"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-b18739545dd8",
                "approver_roles": [
                  "业务经理"
                ]
              }
            ]
          }
        ],
        "workflow_tasks": [
          {
            "ticket": {
              "task_uid": "wf-6b2f45b6e1b2",
              "wf_id": "delivery_risk_flow",
              "title": "出货风险协同工单",
              "status": "running",
              "priority": "P1",
              "assignees": [
                "计划",
                "物控",
                "业务",
                "物流"
              ],
              "context": {
                "run_id": "run-99f31d7a4709",
                "line_code": "SMT-1",
                "product_model": "A型号",
                "step": "delivery_risk_wf",
                "atp_result": {
                  "model": "A型号",
                  "available_qty": 400.0,
                  "demand_qty": 3500.0,
                  "gap_qty": 3100.0,
                  "formula": "on_hand - reserved"
                },
                "risk_list": [
                  {
                    "order_no": "SO-20261015-01",
                    "qty": 2000,
                    "eta": "2026-10-17 10:00:00",
                    "ship_status": "待发货"
                  },
                  {
                    "order_no": "SO-20261010-03",
                    "qty": 1500,
                    "eta": "2026-10-14 10:00:00",
                    "ship_status": "待发货"
                  }
                ]
              },
              "hitl_prompt": "",
              "history": [
                {
                  "ts": "2026-10-17T01:23:42.964238+00:00",
                  "action": "create",
                  "actor": "system",
                  "comment": "",
                  "from": "pending",
                  "to": "pending",
                  "reason": "创建工单"
                },
                {
                  "ts": "2026-10-17T01:23:43.010631+00:00",
                  "action": "start",
                  "actor": "orchestrator",
                  "comment": "出货风险协同工单启动",
                  "from": "pending",
                  "to": "running",
                  "reason": "出货风险协同工单启动"
                }
              ],
              "created_at": "2026-10-17T01:23:42.964204+00:00",
              "updated_at": "2026-10-17T01:23:43.010618+00:00",
              "sla_due_at": "2026-10-17T09:23:42.964204+00:00"
            },
            "notified": [
              "计划",
              "物控",
              "业务",
              "物流"
            ],
            "notice": {
              "level": "P1",
              "targets": [
                "计划",
                "物控",
                "业务",
                "物流"
              ],
              "title": "出货风险协同工单",
              "body": "出货风险协同工单\n请相关角色处理（SLA 8h，超时自动升级）"
            },
            "provenance": [
              {
                "channel": "workflow",
                "task_uid": "wf-6b2f45b6e1b2",
                "wf_id": "delivery_risk_flow",
                "assignees": [
                  "计划",
                  "物控",
                  "业务",
                  "物流"
                ]
              }
            ]
          }
        ],
        "timings": {
          "intake": 0.2,
          "business_order": 7.7,
          "logistics": 8.2,
          "manufacturing_output": 9.0,
          "planning": 8.9,
          "material_control": 9.8,
          "warehouse_atp": 10.2,
          "atp_analysis": 0.2,
          "delivery_risk_wf": 78.8,
          "hitl_delivery_commit": 35.6,
          "report_delivery": 0.4
        },
        "degraded": true,
        "degraded_reasons": [
          "报告生成 LLM 不可用：模板化 + 禁词表兜底"
        ],
        "fallback": null,
        "elapsed_ms": 150.8,
        "error": null
      }
    },
    "SMT-1设备参数异常吗": {
      "events": [
        {
          "type": "agent_start",
          "node": "intake",
          "label": "业务Agent · 意图识别"
        },
        {
          "type": "tool_start",
          "tool": "意图识别",
          "detail": "两级路由（L0 规则快路径 / L1 闭集分类 / 降级模糊匹配）"
        },
        {
          "type": "tool_end",
          "tool": "意图识别",
          "detail": "识别场景：equipment_warning | 型号：A型号 | 线体：SMT-1"
        },
        {
          "type": "agent_end",
          "node": "intake",
          "label": "业务Agent · 意图识别",
          "summary": "识别场景：equipment_warning | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.2
        },
        {
          "type": "agent_start",
          "node": "equipment_check",
          "label": "设备Agent · 查EAM"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询SMT-1线设备异常"
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回3条异常"
        },
        {
          "type": "agent_end",
          "node": "equipment_check",
          "label": "设备Agent · 查EAM",
          "summary": "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）",
          "status": "ok",
          "elapsed_ms": 1.3
        },
        {
          "type": "agent_start",
          "node": "engineering_param",
          "label": "工程 · 参数建议"
        },
        {
          "type": "agent_start",
          "node": "maintenance",
          "label": "制造 · 维保建议"
        },
        {
          "type": "tool_start",
          "tool": "规则模板",
          "detail": "无 LLM/无数据源 → 模板化结论（不编造）"
        },
        {
          "type": "tool_end",
          "tool": "规则模板",
          "detail": "工艺参数基线：无数据源，未编造结论"
        },
        {
          "type": "agent_end",
          "node": "engineering_param",
          "label": "工程 · 参数建议",
          "summary": "工艺参数建议：数据缺口（无参数基线数据源）",
          "status": "degraded",
          "elapsed_ms": 1.6
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 SMT-1 设备异常以生成维保建议"
        },
        {
          "type": "agent_start",
          "node": "planning_window",
          "label": "计划 · 换产窗口"
        },
        {
          "type": "agent_start",
          "node": "spare_parts",
          "label": "采购 · 备件需求"
        },
        {
          "type": "tool_start",
          "tool": "SQL查询",
          "detail": "查询 A型号 排产以评估停机窗口"
        },
        {
          "type": "tool_start",
          "tool": "规则模板",
          "detail": "无 LLM/无数据源 → 模板化结论（不编造）"
        },
        {
          "type": "tool_end",
          "tool": "规则模板",
          "detail": "备件库存：无数据源，未编造结论"
        },
        {
          "type": "agent_end",
          "node": "spare_parts",
          "label": "采购 · 备件需求",
          "summary": "备件需求：数据缺口（无备件数据源）",
          "status": "degraded",
          "elapsed_ms": 1.2
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回3条异常"
        },
        {
          "type": "agent_end",
          "node": "maintenance",
          "label": "制造 · 维保建议",
          "summary": "维保建议 3 项",
          "status": "ok",
          "elapsed_ms": 3.4
        },
        {
          "type": "tool_end",
          "tool": "SQL查询",
          "detail": "返回2条排产"
        },
        {
          "type": "agent_end",
          "node": "planning_window",
          "label": "计划 · 换产窗口",
          "summary": "排产 2 条，需停机窗口",
          "status": "ok",
          "elapsed_ms": 3.2
        },
        {
          "type": "fan_in",
          "node": "fanin",
          "summary": "汇聚 5 域结果（降级: rd_engineering,supply_chain）",
          "domains": [
            "equipment",
            "manufacturing",
            "orchestration",
            "rd_engineering",
            "supply_chain"
          ],
          "degraded": [
            "rd_engineering",
            "supply_chain"
          ]
        },
        {
          "type": "agent_start",
          "node": "maintenance_flow",
          "label": "maintenance_flow"
        },
        {
          "type": "tool_start",
          "tool": "工作流",
          "detail": "创建工单：维保工单（P1，SLA 4h）"
        },
        {
          "type": "tool_end",
          "tool": "工作流",
          "detail": "工单 wf-f66d65cb754c 已创建（P1 通知 设备,工程,计划，SLA 4h）"
        },
        {
          "type": "agent_end",
          "node": "maintenance_flow",
          "label": "maintenance_flow",
          "summary": "工单 wf-f66d65cb754c 已创建（P1 通知 设备,工程,计划，SLA 4h）",
          "status": "ok",
          "elapsed_ms": 64.7
        },
        {
          "type": "agent_start",
          "node": "hitl_maintenance_window",
          "label": "hitl_maintenance_window"
        },
        {
          "type": "agent_end",
          "node": "hitl_maintenance_window",
          "label": "hitl_maintenance_window",
          "summary": "停机窗口确认 → waiting_hitl（审批人：制造现场负责人）",
          "status": "ok",
          "elapsed_ms": 30.3
        },
        {
          "type": "agent_start",
          "node": "report_equipment",
          "label": "报告Agent · 设备简报"
        },
        {
          "type": "agent_end",
          "node": "report_equipment",
          "label": "报告Agent · 设备简报",
          "summary": "内部简报已生成（5 项）；含数据缺口",
          "status": "ok",
          "elapsed_ms": 0.3
        }
      ],
      "result": {
        "query": "SMT-1设备参数异常吗",
        "run_id": "run-abdaa47d4189",
        "route": {
          "domain": "manufacturing",
          "scenario_key": "equipment_warning_v2",
          "confidence": 1.0,
          "level": "L0",
          "entities": {
            "product_model": "A型号",
            "line_code": "SMT-1",
            "urgency": "normal",
            "v1_scenario": "equipment_warning"
          },
          "low_confidence": false,
          "reason": "场景触发词命中: equipment_warning_v2"
        },
        "scenario_key": "equipment_warning_v2",
        "report": "",
        "internal_brief": {
          "设备告警": "近2小时3条设备异常：PRT-01 刮刀压力=0.8MPa（基准0.5）；PRT-01 钢网清洁周期=15min（基准4h）；OVN-02 回流温区3=245℃（基准235±5）",
          "维保建议": "维保建议 3 项",
          "换产窗口": "排产 2 条，需停机窗口",
          "参数建议": "工艺参数建议：数据缺口（无参数基线数据源）",
          "备件需求": "备件需求：数据缺口（无备件数据源）",
          "数据缺口": "工艺参数基线：无数据源，未编造结论；备件库存：无数据源，未编造结论"
        },
        "provenance": [
          {
            "channel": "registered",
            "query_name": "equip_alarms",
            "desc": "EAM：线体设备异常告警（等价 V1 EquipmentAgent）",
            "params": {
              "line": "SMT-1"
            },
            "tables": [
              "equipment_log"
            ],
            "row_count": 3,
            "elapsed_ms": 1.1,
            "sql": "SELECT equipment_id, alarm_code, param_name, param_value FROM equipment_log WHERE line_code = :line AND is_abnormal = 1",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "equipment"
          },
          {
            "channel": "hitl",
            "task_uid": "wf-95c419fa4013",
            "approver_roles": [
              "制造现场负责人"
            ],
            "domain": "hitl"
          },
          {
            "channel": "registered",
            "query_name": "equip_alarms",
            "desc": "EAM：线体设备异常告警（等价 V1 EquipmentAgent）",
            "params": {
              "line": "SMT-1"
            },
            "tables": [
              "equipment_log"
            ],
            "row_count": 3,
            "elapsed_ms": 2.5,
            "sql": "SELECT equipment_id, alarm_code, param_name, param_value FROM equipment_log WHERE line_code = :line AND is_abnormal = 1",
            "masked": false,
            "confidence": 0.85,
            "fallback": null,
            "warnings": [],
            "error": null,
            "domain": "manufacturing"
          },
          {
            "channel": "template",
            "domain": "rd_engineering",
            "degraded": true,
            "note": "规则模板生成（无 LLM/无数据源）"
          },
          {
            "channel": "data_gap",
            "domain": "rd_engineering",
            "note": "工艺参数基线：无数据源，未编造结论"
          },
          {
            "channel": "report",
            "scenario": "equipment_warning_v2",
            "degraded": true,
            "masked_words": [
              "设备故障",
              "供应商来料不良",
              "品质不良超标",
              "停线",
              "根因",
              "维修"
            ],
            "domain": "report"
          },
          {
            "channel": "data_gap",
            "domain": "report",
            "note": "工艺参数基线：无数据源，未编造结论"
          },
          {
            "channel": "data_gap",
            "domain": "report",
            "note": "备件库存：无数据源，未编造结论"
          },
          {
            "channel": "template",
            "domain": "supply_chain",
            "degraded": true,
            "note": "规则模板生成（无 LLM/无数据源）"
          },
          {
            "channel": "data_gap",
            "domain": "supply_chain",
            "note": "备件库存：无数据源，未编造结论"
          },
          {
            "channel": "workflow",
            "task_uid": "wf-f66d65cb754c",
            "wf_id": "maintenance_flow",
            "assignees": [
              "设备",
              "工程",
              "计划"
            ],
            "domain": "workflow"
          },
          {
            "channel": "node_timing",
            "node": "engineering_param",
            "elapsed_ms": 1.6,
            "status": "degraded"
          },
          {
            "channel": "node_timing",
            "node": "equipment_check",
            "elapsed_ms": 1.3,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "hitl_maintenance_window",
            "elapsed_ms": 30.3,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "intake",
            "elapsed_ms": 0.2,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "maintenance",
            "elapsed_ms": 3.4,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "maintenance_flow",
            "elapsed_ms": 64.7,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "planning_window",
            "elapsed_ms": 3.2,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "report_equipment",
            "elapsed_ms": 0.3,
            "status": "ok"
          },
          {
            "channel": "node_timing",
            "node": "spare_parts",
            "elapsed_ms": 1.2,
            "status": "degraded"
          }
        ],
        "hitl": [
          {
            "step": "hitl_maintenance_window",
            "kind": "maintenance_window",
            "prompt": "停机窗口确认",
            "status": "waiting_hitl",
            "approver_roles": [
              "制造现场负责人"
            ],
            "actions": [
              {
                "key": "confirm_window",
                "label": "确认窗口"
              },
              {
                "key": "adjust_window",
                "label": "调整窗口"
              }
            ],
            "task_uid": "wf-95c419fa4013",
            "card": {
              "title": "[待确认] 停机窗口确认",
              "body": "停机窗口确认\n线体: SMT-1 | 型号: A型号\nFA评级: ? | 预估复线: ?h\n审批人: 制造现场负责人",
              "actions": [
                {
                  "key": "confirm_window",
                  "label": "确认窗口"
                },
                {
                  "key": "adjust_window",
                  "label": "调整窗口"
                }
              ],
              "level": "P1"
            },
            "provenance": [
              {
                "channel": "hitl",
                "task_uid": "wf-95c419fa4013",
                "approver_roles": [
                  "制造现场负责人"
                ]
              }
            ]
          }
        ],
        "workflow_tasks": [
          {
            "ticket": {
              "task_uid": "wf-f66d65cb754c",
              "wf_id": "maintenance_flow",
              "title": "维保工单",
              "status": "running",
              "priority": "P1",
              "assignees": [
                "设备",
                "工程",
                "计划"
              ],
              "context": {
                "run_id": "run-abdaa47d4189",
                "line_code": "SMT-1",
                "product_model": "A型号",
                "step": "maintenance_flow",
                "has_alarm": true
              },
              "hitl_prompt": "",
              "history": [
                {
                  "ts": "2026-10-17T01:23:43.106581+00:00",
                  "action": "create",
                  "actor": "system",
                  "comment": "",
                  "from": "pending",
                  "to": "pending",
                  "reason": "创建工单"
                },
                {
                  "ts": "2026-10-17T01:23:43.140331+00:00",
                  "action": "start",
                  "actor": "orchestrator",
                  "comment": "维保工单启动",
                  "from": "pending",
                  "to": "running",
                  "reason": "维保工单启动"
                }
              ],
              "created_at": "2026-10-17T01:23:43.106552+00:00",
              "updated_at": "2026-10-17T01:23:43.140316+00:00",
              "sla_due_at": "2026-10-17T05:23:43.106552+00:00"
            },
            "notified": [
              "设备",
              "工程",
              "计划"
            ],
            "notice": {
              "level": "P1",
              "targets": [
                "设备",
                "工程",
                "计划"
              ],
              "title": "维保工单",
              "body": "维保工单\n请相关角色处理（SLA 4h，超时自动升级）"
            },
            "provenance": [
              {
                "channel": "workflow",
                "task_uid": "wf-f66d65cb754c",
                "wf_id": "maintenance_flow",
                "assignees": [
                  "设备",
                  "工程",
                  "计划"
                ]
              }
            ]
          }
        ],
        "timings": {
          "intake": 0.2,
          "equipment_check": 1.3,
          "engineering_param": 1.6,
          "spare_parts": 1.2,
          "maintenance": 3.4,
          "planning_window": 3.2,
          "maintenance_flow": 64.7,
          "hitl_maintenance_window": 30.3,
          "report_equipment": 0.3
        },
        "degraded": true,
        "degraded_reasons": [
          "报告生成 LLM 不可用：模板化 + 禁词表兜底"
        ],
        "fallback": null,
        "elapsed_ms": 123.2,
        "error": null
      }
    }
  };

  /** 向后兼容：默认回放 = line_stop 场景 */
  const CHAT_DEMO = CHAT_DEMOS["A型号在SMT-1停线，何时复线？"];

  window.DEMO = {
    ROLES, ALERT_SUMMARY, DEMO_TODOS, ORDERS, ALERTS, REPORTS, DEMO_HITL, CHAT_DEMO, CHAT_DEMOS,
  };
})();
