/**
 * 演示数据模块（demo-data.js）
 *
 * 说明：本文件仅承载"演示数据"——看板 / 预警 / 报告 / 后端离线时的问数回放。
 * 问数 / 事件流 / 人工确认 审批在真实后端可达时一律走真实链路（见 app.js / screens.js），
 * 演示数据必须与真实链路输出保持同构（字段结构一致），切换时 UI 零适配。
 * 语义素材取自项目真实场景：A型号 / SMT-1 / 锡膏偏移 / 千住金属 / 深南电路 /
 * 华为终端 / BATCH-A-1008 / wf- 工单号 / PRT-01 刮刀压力 / OVN-02 回流焊。
 */
(function () {
  "use strict";

  /** 角色（演示：切换只影响看板数据范围与待办列表）。
   *  uid：稳定用户标识——一人一签（G6）按 uid 判定，不按姓名。
   *  每个岗位对应**一个**治理角色：同一身份持多个会签角色 = 会签造假入口。
   *  演示三方会签的正确方式是换身份登录。 */
  /** 登录岗位按域分 5 组（renderLogin 渲染组标题 · sticky）。
   *  仅影响登录页排版；ROLES 仍是单一事实源数组。 */
  const ROLE_GROUPS = [
    { id: "mgmt",   name: "经营层",       roles: ["gm"] },
    { id: "quality", name: "质量域",      roles: ["qd", "sqe", "pqe", "qa"] },
    { id: "make",   name: "制造与设备",    roles: ["mfg", "eqm", "eq", "mt", "ehs"] },
    { id: "eng",    name: "研发与工程",    roles: ["rdm", "rd", "pe", "ie", "npi", "pm", "te", "me", "rdt"] },
    { id: "func",   name: "供应链与职能",  roles: ["scm", "pln", "mc", "procm", "proc", "whm", "wh", "biz", "finm", "fin", "hr", "cs"] },
  ];

  const ROLES = [
    { id: "gm",  name: "总经理",   abbr: "GM",    uid: "u-gm",  user: "张明", dept: "management",    roles: ["gm"],
      greeting: "全局运营视图", desc: "汇总五大域组运行态势，聚焦订单履约风险与升级事项",
      scope: "五域全局 · 只读汇总", scenarios: ["经营异常摘要", "跨部门升级", "订单履约风险"], tone: "blue" },
    { id: "mfg", name: "制造经理", abbr: "MFG",   uid: "u-mfg", user: "李伟", dept: "manufacturing", roles: ["manufacturing"],
      greeting: "制造运营视图", desc: "聚焦停线 / OEE / 设备告警 / 复线进度，制造域工单优先",
      scope: "制造 · 设备 · 停线", scenarios: ["停线应急", "设备预警", "OEE 达成"], tone: "green" },
    { id: "eq",  name: "EQ 设备工程", abbr: "EQ",  uid: "u-eq",  user: "郑昊", dept: "equipment",     roles: ["equipment"],
      greeting: "设备保障视图", desc: "聚焦设备告警 / 停机窗口 / 备件与 OEE 损耗，与制造经理共同决定设备能不能开、停多久",
      scope: "设备 · 停机窗口 · OEE 损耗", scenarios: ["停机窗口确认", "设备预警", "OEE 损耗分析"], tone: "teal" },
    { id: "sqe", name: "SQE 供应商质量", abbr: "SQE", uid: "u-sqe", user: "刘强", dept: "quality", roles: ["sqe"],
      greeting: "供应商质量视图", desc: "聚焦来料异常 / SCAR / 供应商处置发起（退货、换货、特采）",
      scope: "来料 · 供应商 · SCAR", scenarios: ["来料异常处置", "SCAR 跟催", "特采发起"], tone: "amber" },
    { id: "pqe", name: "PQE 制程质量", abbr: "PQE", uid: "u-pqe", user: "王倩", dept: "quality", roles: ["pqe"],
      greeting: "品质工程视图", desc: "聚焦 SPC / 不良分布 / 客诉 8D 会签，敏感字段按角色脱敏",
      scope: "质量域 · 供应商字段脱敏", scenarios: ["SPC / CPK", "客诉 8D", "不良分析"], tone: "amber" },
    { id: "qa",  name: "QA 质量保证", abbr: "QA",  uid: "u-qa",  user: "李静", dept: "quality", roles: ["qa"],
      greeting: "质量保证视图", desc: "聚焦合规把关 / 放行审核 / 8D 关闭会签",
      scope: "合规 · 放行 · 审计", scenarios: ["放行审核", "8D 关闭", "质量体系"], tone: "olive" },
    { id: "rd",  name: "研发工程师", abbr: "RD", uid: "u-rd",  user: "林哲", dept: "rd_engineering", roles: ["rd_engineering"],
      greeting: "研发工程视图", desc: "聚焦 ECN / NPI / 根因分析（BOM 等数据源未接入时诚实标注缺口）",
      scope: "BOM · ECN · 工艺（部分无数据源）", scenarios: ["ECN 变更", "NPI 导入", "根因分析"], tone: "indigo" },
    { id: "pe",  name: "PE 产品/工艺工程", abbr: "PE", uid: "u-pe", user: "袁磊", dept: "rd_engineering", roles: ["product_engineering"],
      greeting: "制程与量产视图", desc: "聚焦可制造性 / 制程窗口 / 试产技术结论 / PFMEA，把 RD 设计变成能量产的产品",
      scope: "制程 · 可制造性 · 量产承接", scenarios: ["DFM 评审", "制程参数与 CPK", "试产技术结论"], tone: "purple" },
    { id: "pln", name: "PLN 计划", abbr: "PLN", uid: "u-pln", user: "吴桐", dept: "supply_chain",  roles: ["planning"],
      greeting: "产销协同视图", desc: "聚焦 MPS 排产 / 产能负荷 / 齐套 / 插单，S&OP 出交付承诺的技术背书",
      scope: "排产 · 产能 · S&OP", scenarios: ["MPS 排产", "产能负荷", "插单与改期"], tone: "blue" },
    { id: "mc",  name: "物控专员", abbr: "MC",  uid: "u-mc",  user: "赵敏", dept: "supply_chain",  roles: ["material_control"],
      greeting: "供应链视图", desc: "聚焦缺料 / 齐套率 / 到货批次，供应链域待办优先",
      scope: "物料 · 库存 · 齐套", scenarios: ["缺料预警", "齐套检查", "到货批次"], tone: "teal" },
    { id: "proc", name: "采购专员", abbr: "PROC", uid: "u-proc", user: "孙立", dept: "procurement",   roles: ["procurement"],
      greeting: "采购与供应商视图", desc: "聚焦来料异常会签 / SCAR 跟催 / 供应商索赔，价格字段可见",
      scope: "供应商 · 价格（可见）", scenarios: ["来料会签", "SCAR 跟催", "供应商索赔"], tone: "coral" },
    { id: "wh",  name: "仓库主管", abbr: "WH",  uid: "u-wh",  user: "周凯", dept: "warehouse",     roles: ["warehouse"],
      greeting: "仓储与发货视图", desc: "聚焦库存准确 / ATP 可承诺量 / 发货协同",
      scope: "库存 · 出入库 · 发货", scenarios: ["ATP 可承诺量", "发货协同", "库存准确"], tone: "gray" },
    { id: "biz", name: "业务经理", abbr: "SALES", uid: "u-biz", user: "陈皓", dept: "sales",         roles: ["sales"],
      greeting: "业务交付视图", desc: "聚焦对客 ETA / 订单临期 / 客诉响应，业务域待办优先",
      scope: "订单 · 对客 ETA · 出货", scenarios: ["交期承诺", "客诉响应", "出货风险"], tone: "purple" },
    { id: "fin", name: "FIN 财务成本", abbr: "FIN", uid: "u-fin", user: "何静", dept: "finance",       roles: ["finance"],
      greeting: "成本与索赔视图", desc: "聚焦 BOM 成本 / 制造费用 / COPQ，来料处置的金额口径由财务会签",
      scope: "成本 · 价格（最敏感）· COPQ", scenarios: ["BOM 成本核算", "供应商索赔金额", "质量成本分析"], tone: "gray" },

    // ---------- 部门负责人（每个职能部门一个经理/总监岗位；一线角色的对口审批/终批视角） ----------
    { id: "qd",  name: "质量总监",   abbr: "QD",    uid: "u-qd",  user: "沈玉", dept: "quality",       roles: ["quality_director"],
      greeting: "质量决策视图", desc: "聚焦质量成本 / 客诉升级 / 8D 终批与不合格品处置终裁，质量域最高决策口径",
      scope: "质量全域 · 终批裁量", scenarios: ["8D 终批", "不合格品处置终裁", "客诉升级裁决"], tone: "amber" },
    { id: "eqm", name: "设备经理",   abbr: "EQM",   uid: "u-eqm", user: "冯磊", dept: "equipment",     roles: ["equipment_manager"],
      greeting: "设备管理视图", desc: "聚焦设备可用率 / 停机窗口审批 / 备件与维保预算，停机开窗的最终审批人",
      scope: "设备 · 停机窗口（可审批）", scenarios: ["停机窗口审批", "设备可用率", "维保计划"], tone: "teal" },
    { id: "rdm", name: "研发经理",   abbr: "RDM",   uid: "u-rdm", user: "徐峰", dept: "rd_engineering", roles: ["rd_manager"],
      greeting: "研发管理视图", desc: "聚焦 NPI 里程碑 / ECN 变更裁决 / 研发质量闭环与资源",
      scope: "研发 · NPI · ECN（可裁决）", scenarios: ["NPI 里程碑", "ECN 变更裁决", "研发质量闭环"], tone: "indigo" },
    { id: "scm", name: "供应链经理", abbr: "SCM",   uid: "u-scm", user: "罗敏", dept: "supply_chain",  roles: ["supply_chain_manager"],
      greeting: "供应链交付视图", desc: "聚焦 S&OP / 交付承诺终批 / 缺料与齐套风险，供应链域交付口径",
      scope: "计划 · 物控 · 交付承诺（可终批）", scenarios: ["交付承诺终批", "S&OP 评审", "齐套风险升级"], tone: "blue" },
    { id: "procm", name: "采购经理", abbr: "PROC-M", uid: "u-procm", user: "韩雪", dept: "procurement",  roles: ["procurement_manager"],
      greeting: "采购管理视图", desc: "聚焦供应商绩效 / 采购策略 / 来料处置会签，价格与供应商字段可见",
      scope: "供应商 · 价格（可见）· 采购策略", scenarios: ["来料处置会签", "供应商绩效", "采购策略"], tone: "coral" },
    { id: "whm", name: "仓储经理",   abbr: "WH-M",  uid: "u-whm", user: "蒋斌", dept: "warehouse",     roles: ["warehouse_manager"],
      greeting: "仓储管理视图", desc: "聚焦库存周转 / 出入库准确 / 发货协同终批，仓储域运营口径",
      scope: "库存 · 出入库 · 发货（可终批）", scenarios: ["发货协同终批", "库存周转", "盘点差异"], tone: "gray" },
    { id: "finm", name: "财务经理",  abbr: "FIN-M", uid: "u-finm", user: "秦岚", dept: "finance",      roles: ["finance_manager"],
      greeting: "财务管理视图", desc: "聚焦成本核算 / COPQ / 索赔金额口径终批，价格字段可见",
      scope: "成本 · 价格（可见）· COPQ", scenarios: ["成本核算", "索赔金额终批", "质量成本分析"], tone: "gray" },

    // ---------- 研发/工程/项目岗位（P0：补齐"有智能体无门户岗位"的门面缺口） ----------
    // 说明：IE / NPI / PM / TE / ME / 研发测试 / 行政人事 七个智能体此前已在
    // agents/agentic.py 注册，但门户缺对应登录岗位；本段补齐，使「一人一岗一治理角色」全覆盖。
    { id: "ie",  name: "IE 工业工程",   abbr: "IE",  uid: "u-ie",  user: "高翔", dept: "rd_engineering", roles: ["ie_engineer"],
      greeting: "工业工程视图", desc: "聚焦标准工时 / 产能测算 / 线平衡 / 效率改善与人力配置",
      scope: "工时 · 产能 · 线平衡", scenarios: ["标准工时测算", "线平衡改善", "产能与人力配置"], tone: "teal" },
    { id: "npi", name: "NPI 导入工程师", abbr: "NPI", uid: "u-npi", user: "田甜", dept: "rd_engineering", roles: ["npi_engineer"],
      greeting: "新品导入视图", desc: "聚焦 EVT/DVT/PVT/MP 阶段 / 试产安排 / DFM 问题 / 导入进度与风险",
      scope: "NPI · 试产 · DFM", scenarios: ["阶段转段判定", "试产安排", "DFM 评审"], tone: "indigo" },
    { id: "pm",  name: "项目经理",     abbr: "PM",  uid: "u-pm",  user: "陈锋", dept: "pm",             roles: ["project_manager"],
      greeting: "项目管理视图", desc: "聚焦跨部门里程碑 / 风险跟踪 / 资源冲突 / 项目健康度",
      scope: "项目 · 里程碑 · 风险", scenarios: ["里程碑跟踪", "跨部门协调", "项目风险升级"], tone: "blue" },
    { id: "te",  name: "TE 工程测试",  abbr: "TE",  uid: "u-te",  user: "许阳", dept: "rd_engineering", roles: ["test_engineer"],
      greeting: "工程测试视图", desc: "聚焦测试执行 / 测试报告 / 失效分析 / 可靠性测试（跌落、高低温、寿命）",
      scope: "测试执行 · 可靠性 · 失效分析", scenarios: ["可靠性测试", "测试报告", "失效分析"], tone: "purple" },
    { id: "me",  name: "ME 结构工程",  abbr: "ME",  uid: "u-me",  user: "邱帆", dept: "rd_engineering", roles: ["me_engineer"],
      greeting: "结构工程视图", desc: "聚焦结构设计 / 模具 / 公差配合 / 装配与变形断裂问题",
      scope: "结构 · 模具 · 公差（CAD 源未接入）", scenarios: ["结构设计评审", "模具与公差", "装配问题分析"], tone: "olive" },
    { id: "rdt", name: "研发测试工程师", abbr: "RD-T", uid: "u-rdt", user: "谭睿", dept: "rd_engineering", roles: ["rd_test_engineer"],
      greeting: "研发测试视图", desc: "聚焦软件 / 固件测试 / Bug 管理 / 版本回归（Bug 系统源未接入）",
      scope: "软件测试 · 固件 · Bug", scenarios: ["版本回归", "Bug 管理与判定", "固件验证"], tone: "indigo" },
    { id: "hr",  name: "行政人事",     abbr: "HR",  uid: "u-hr",  user: "尹姿", dept: "admin",          roles: ["admin_hr"],
      greeting: "行政人事视图", desc: "聚焦行政流程（用章 / 会议室 / 访客）/ 培训与资质 / 考勤假期",
      scope: "行政 · 人事 · 培训", scenarios: ["用章审批", "培训与资质", "考勤假期"], tone: "gray" },

    // ---------- 安全环保 / 客户服务 / 设备维保岗位（P1：补齐部门岗位与治理角色） ----------
    { id: "ehs", name: "EHS 安全环保", abbr: "EHS", uid: "u-ehs", user: "孟浩", dept: "ehs",          roles: ["ehs"],
      greeting: "安全环保视图", desc: "聚焦安全隐患 / 环保危废 / 消防与职业健康 / 事故闭环",
      scope: "安全 · 环保 · 职业健康", scenarios: ["安全隐患排查", "危废合规", "事故调查与闭环"], tone: "amber" },
    { id: "cs",  name: "客户服务",     abbr: "CS",  uid: "u-cs",  user: "苏晴", dept: "service",       roles: ["customer_service"],
      greeting: "客户服务视图", desc: "聚焦客诉受理 / 售后与退换货 / 服务时效 / 对客口径协同",
      scope: "客诉受理 · 售后 · 服务时效", scenarios: ["客诉受理分派", "售后与退换货", "服务时效跟进"], tone: "purple" },
    { id: "mt",  name: "设备维保技师", abbr: "MT",  uid: "u-mt",  user: "陆凯", dept: "equipment",     roles: ["maintenance_tech"],
      greeting: "设备维保视图", desc: "聚焦点检 / 保养 / 维修工单执行 / 备件领用与停机窗口发起",
      scope: "点检 · 保养 · 维修执行", scenarios: ["点检保养计划", "维修工单执行", "停机窗口发起"], tone: "teal" },
  ];

  /** 工作台 · 今日预警摘要（按角色取不同范围，演示数据） */
  /** 各岗位看板 KPI 卡（数据驱动：站在岗位角度定义"打开页面第一眼看什么"）。
   *  每岗位 3 张专属卡 + 1 张通用"待人工确认"卡（screens.js 固定渲染）。
   *  接真实数据后：值改由 GET /api/v2/metrics?role= 下发，配置结构不变。 */
  const ROLE_KPIS = {
    gm: [
      { icon: "orders", tone: "blue", num: "92.4%", label: "订单准交率 OTD", trend: "近 4 周 +1.8pct", up: false , ask: "最近一周有哪些订单存在延期风险？", drill: { screen: "orders", kpi: "otd" }},
      { icon: "activity", tone: "red", num: "386", label: "客诉 PPM（本月）", trend: "华为终端 S 级订单临期", up: true , ask: "本月客诉集中在哪些问题？"},
      { icon: "warn", tone: "red", num: "5", label: "跨部门风险工单", trend: "2 张待升级", up: true , ask: "现在有哪些跨部门工单超时了？"},
    ],
    mfg: [
      { icon: "activity", tone: "amber", num: "78.6%", label: "设备综合效率 OEE", trend: "SMT-1 停线恢复中", up: false , ask: "SMT-1现在设备参数异常吗？", drill: { screen: "manufacturing", kpi: "oee" }},
      { icon: "clock", tone: "red", num: "2.4h", label: "今日累计停线", trend: "刮刀压力异常已定位", up: true , ask: "A型号在SMT-1停线，何时复线？"},
      { icon: "check", tone: "green", num: "96.2%", label: "直通率 FPY", trend: "环比 +0.4pct", up: false , ask: "今天的直通率FPY是多少？", drill: { screen: "quality", kpi: "fpy" }},
    ],
    sqe: [
      { icon: "box", tone: "amber", num: "2", label: "来料不合格批次", trend: "锡膏/IC 批次处置中", up: true , ask: "今天锡膏批次有问题", drill: { screen: "quality", kpi: "incoming_pass_rate" }},
      { icon: "warn", tone: "red", num: "3", label: "SCAR 未关闭", trend: "1 张 SLA 剩余 6h", up: true , ask: "来料不合格批次有哪些需要处置？"},
      { icon: "check", tone: "blue", num: "1", label: "本月特采笔数", trend: "全部留痕可追溯", up: false , ask: "最近有多少特采放行？"},
    ],
    pqe: [
      { icon: "activity", tone: "red", num: "0.89", label: "锡膏印刷 CPK", trend: "低于 1.33 改善中", up: true , ask: "今天锡膏批次有问题"},
      { icon: "warn", tone: "amber", num: "77pcs", label: "主导不良（锡膏偏移）", trend: "贴片偏移 46pcs 已围堵", up: true , ask: "华为投诉BATCH-A-1008", drill: { screen: "quality", kpi: "defect_rate" }},
      { icon: "approve", tone: "blue", num: "2", label: "8D 在办", trend: "1 张待我会签", up: false , ask: "锡膏印刷CPK最近趋势如何？"},
    ],
    qa: [
      { icon: "check", tone: "blue", num: "18", label: "今日放行批次", trend: "全部留痕", up: false , ask: "今天锡膏批次有问题"},
      { icon: "warn", tone: "amber", num: "2", label: "待合规审核项", trend: "特采 1 笔需合规确认", up: true , ask: "华为投诉BATCH-A-1008", drill: { screen: "quality", kpi: "std_conformity" }},
      { icon: "clock", tone: "red", num: "1", label: "8D 待关闭", trend: "D8 关闭待质量总监", up: true , ask: "8D报告哪些待关闭？"},
    ],
    mc: [
      { icon: "box", tone: "red", num: "3", label: "缺料项", trend: "锡膏 M7020 延迟 36h", up: true , ask: "A型号能发多少货", drill: { screen: "orders", kpi: "material_shortage_rate" }},
      { icon: "activity", tone: "amber", num: "87%", label: "齐套率", trend: "A 型号欠 3100pcs", up: false , ask: "锡膏M7020什么时候到货？", drill: { screen: "orders", kpi: "kit_rate" }},
      { icon: "clock", tone: "blue", num: "4", label: "在途批次", trend: "预计 48h 内到货", up: false , ask: "A型号缺料影响哪些订单？"},
    ],
    biz: [
      { icon: "orders", tone: "blue", num: "92.4%", label: "OTD 准交率", trend: "环比 +1.8pct", up: false , ask: "A型号能发多少货", drill: { screen: "orders", kpi: "otd" }},
      { icon: "warn", tone: "red", num: "2", label: "临期订单", trend: "华为 S 级需对客口径", up: true , ask: "华为投诉BATCH-A-1008", drill: { screen: "orders", kpi: "delay_rate" }},
      { icon: "clock", tone: "amber", num: "1.6h", label: "客诉平均响应", trend: "目标 ≤2h", up: false , ask: "哪些订单本周临期？"},
    ],
    proc: [
      { icon: "check", tone: "green", num: "94.1%", label: "供应商交期达成", trend: "千住金属延迟 1 批", up: false , ask: "今天锡膏批次有问题", drill: { screen: "quality", kpi: "incoming_pass_rate" }},
      { icon: "activity", tone: "amber", num: "1", label: "索赔在途", trend: "来料不合格费用分摊中", up: true , ask: "千住金属最近的交期达成情况？"},
      { icon: "box", tone: "blue", num: "0", label: "价格异动", trend: "本季无", up: false , ask: "来料不合格如何索赔？"},
    ],
    wh: [
      { icon: "check", tone: "green", num: "99.2%", label: "库存准确率", trend: "月盘差异 3 项", up: false , ask: "A型号能发多少货", drill: { screen: "orders", kpi: "material_shortage_rate" }},
      { icon: "box", tone: "blue", num: "142", label: "今日出入库", trend: "出货 6 单待拣", up: false , ask: "A型号ATP可承诺量是多少？"},
      { icon: "warn", tone: "amber", num: "1", label: "待发货协同", trend: "A 型号 ATP 400pcs", up: true , ask: "今天有哪些发货单待拣？"},
    ],
    rd: [
      { icon: "clock", tone: "blue", num: "3", label: "ECN 在办", trend: "2 张待会签", up: false , ask: "SMT-1设备参数异常吗？"},
      { icon: "activity", tone: "green", num: "DVT", label: "NPI 当前阶段", trend: "B 型号按计划", up: false , ask: "B型号NPI进展如何？", drill: { screen: "orders", kpi: "milestone_done_rate" }},
      { icon: "warn", tone: "amber", num: "2", label: "根因待补数据", trend: "BOM/工艺参数源未接入", up: true , ask: "BOM/ECN数据为什么缺失？"},
    ],
    eq: [
      { icon: "warn", tone: "red", num: "2", label: "设备预警", trend: "SMT-1 刮刀压力异常", up: true , ask: "SMT-1现在设备参数异常吗？", drill: { screen: "manufacturing", kpi: "availability" }},
      { icon: "clock", tone: "amber", num: "1", label: "停机窗口待确认", trend: "PRT-01 点检后开窗", up: true , ask: "A型号在SMT-1停线，何时复线？"},
      { icon: "activity", tone: "green", num: "76.4%", label: "设备综合效率 OEE", trend: "低于目标 2.2pct", up: false , ask: "设备综合效率OEE是多少？", drill: { screen: "manufacturing", kpi: "oee" }},
    ],
    pe: [
      { icon: "approve", tone: "blue", num: "1", label: "8D 待我会签", trend: "制程根因结论", up: false , ask: "锡膏印刷CPK最近趋势如何？", drill: { screen: "orders", kpi: "task_backlog_rate" }},
      { icon: "activity", tone: "amber", num: "0.89", label: "锡膏印刷 CPK", trend: "工艺窗口待收敛", up: true , ask: "今天锡膏批次有问题"},
      { icon: "warn", tone: "red", num: "1", label: "DFM 待评审", trend: "B 型号结构改版", up: true , ask: "B型号DFM评审进展如何？"},
    ],
    pln: [
      { icon: "orders", tone: "blue", num: "92.4%", label: "订单准交率 OTD", trend: "本周排产已锁定", up: false , ask: "最近一周有哪些订单存在延期风险？", drill: { screen: "orders", kpi: "otd" }},
      { icon: "activity", tone: "amber", num: "3", label: "产能瓶颈工序", trend: "组装段负荷 112%", up: true , ask: "当前产能负荷如何？"},
      { icon: "warn", tone: "red", num: "1", label: "紧急插单待评估", trend: "S 级客户加急 500pcs", up: true , ask: "有哪些订单需要插单或改期？"},
    ],
    fin: [
      { icon: "box", tone: "blue", num: "¥ 86.4", label: "A 型号 BOM 成本", trend: "环比 -1.2%", up: false , ask: "BOM成本是多少？"},
      { icon: "warn", tone: "amber", num: "2", label: "待核算索赔", trend: "供应商 SCAR 赔付中", up: true , ask: "供应商索赔金额是多少？", drill: { screen: "orders", kpi: "task_backlog_rate" }},
      { icon: "activity", tone: "red", num: "¥ 31.2万", label: "本月质量成本 COPQ", trend: "返工+报废占比 62%", up: true , ask: "质量成本COPQ分析"},
    ],
    // ---------- 部门负责人看板（决策/审批视角） ----------
    qd: [
      { icon: "approve", tone: "amber", num: "2", label: "8D 待终批", trend: "1 张 SLA 剩余 6h", up: true , ask: "8D报告哪些待关闭？", drill: { screen: "orders", kpi: "task_backlog_rate" }},
      { icon: "warn", tone: "red", num: "386", label: "客诉 PPM（本月）", trend: "华为终端 S 级升级", up: true , ask: "本月客诉集中在哪些问题？"},
      { icon: "activity", tone: "amber", num: "1", label: "不合格品处置待终裁", trend: "锡膏批次退货 1 批", up: true , ask: "来料不合格批次有哪些需要处置？"},
    ],
    eqm: [
      { icon: "warn", tone: "red", num: "2", label: "设备预警", trend: "SMT-1 刮刀压力异常", up: true , ask: "SMT-1现在设备参数异常吗？", drill: { screen: "manufacturing", kpi: "availability" }},
      { icon: "approve", tone: "amber", num: "1", label: "停机窗口待审批", trend: "PRT-01 点检后开窗", up: true , ask: "A型号在SMT-1停线，何时复线？"},
      { icon: "activity", tone: "green", num: "76.4%", label: "设备可用率 OEE", trend: "低于目标 2.2pct", up: false , ask: "设备综合效率OEE是多少？", drill: { screen: "manufacturing", kpi: "oee" }},
    ],
    rdm: [
      { icon: "clock", tone: "blue", num: "3", label: "ECN 在办", trend: "2 张待我会签", up: false , ask: "SMT-1设备参数异常吗？"},
      { icon: "activity", tone: "green", num: "DVT", label: "NPI 里程碑", trend: "B 型号按计划", up: false , ask: "B型号NPI进展如何？", drill: { screen: "orders", kpi: "milestone_done_rate" }},
      { icon: "approve", tone: "blue", num: "2", label: "研发会签待办", trend: "8D 根因结论待确认", up: false , ask: "锡膏印刷CPK最近趋势如何？"},
    ],
    scm: [
      { icon: "orders", tone: "blue", num: "92.4%", label: "订单准交率 OTD", trend: "本周排产已锁定", up: false , ask: "最近一周有哪些订单存在延期风险？", drill: { screen: "orders", kpi: "otd" }},
      { icon: "approve", tone: "blue", num: "1", label: "交付承诺待终批", trend: "华为 S 级对客 ETA", up: true , ask: "A型号能发多少货"},
      { icon: "box", tone: "red", num: "3", label: "缺料项", trend: "锡膏 M7020 延迟 36h", up: true , ask: "A型号缺料影响哪些订单？", drill: { screen: "orders", kpi: "material_shortage_rate" }},
    ],
    procm: [
      { icon: "check", tone: "green", num: "94.1%", label: "供应商交期达成", trend: "千住金属延迟 1 批", up: false , ask: "千住金属最近的交期达成情况？", drill: { screen: "quality", kpi: "incoming_pass_rate" }},
      { icon: "approve", tone: "amber", num: "1", label: "来料处置待会签", trend: "退货 / 特采二选一", up: true , ask: "来料不合格批次有哪些需要处置？"},
      { icon: "activity", tone: "amber", num: "1", label: "索赔在途", trend: "来料不合格费用分摊中", up: true , ask: "来料不合格如何索赔？"},
    ],
    whm: [
      { icon: "check", tone: "green", num: "99.2%", label: "库存准确率", trend: "月盘差异 3 项", up: false , ask: "A型号能发多少货", drill: { screen: "orders", kpi: "material_shortage_rate" }},
      { icon: "approve", tone: "amber", num: "1", label: "发货协同待终批", trend: "A 型号 ATP 400pcs", up: true , ask: "今天有哪些发货单待拣？"},
      { icon: "box", tone: "blue", num: "142", label: "今日出入库", trend: "出货 6 单待拣", up: false , ask: "A型号ATP可承诺量是多少？"},
    ],
    finm: [
      { icon: "box", tone: "blue", num: "¥ 86.4", label: "A 型号 BOM 成本", trend: "环比 -1.2%", up: false , ask: "BOM成本是多少？"},
      { icon: "approve", tone: "amber", num: "2", label: "索赔金额待终批", trend: "SCAR 赔付口径确认", up: true , ask: "供应商索赔金额是多少？", drill: { screen: "orders", kpi: "task_backlog_rate" }},
      { icon: "activity", tone: "red", num: "¥ 31.2万", label: "本月质量成本 COPQ", trend: "返工+报废占比 62%", up: true , ask: "质量成本COPQ分析"},
    ],
    // ---------- 研发/工程/项目岗位看板（P0） ----------
    ie: [
      { icon: "clock", tone: "blue", num: "42.6h", label: "标准工时（A型号）", trend: "较上版 -3.2h", up: false , ask: "A型号的标准工时是多少？"},
      { icon: "activity", tone: "amber", num: "112%", label: "组装段负荷", trend: "瓶颈工序待平衡", up: true , ask: "当前产能负荷如何？", drill: { screen: "manufacturing", kpi: "production_achieve" }},
      { icon: "check", tone: "green", num: "82.4%", label: "线平衡率", trend: "目标 ≥85%", up: false , ask: "A型号产线线平衡率多少？"},
    ],
    npi: [
      { icon: "activity", tone: "green", num: "DVT", label: "NPI 当前阶段（B型号）", trend: "按计划推进", up: false , ask: "B型号NPI进展如何？", drill: { screen: "orders", kpi: "milestone_done_rate" }},
      { icon: "clock", tone: "blue", num: "2", label: "待办试产批", trend: "PVT 试产排程中", up: false , ask: "B型号试产安排如何？"},
      { icon: "warn", tone: "amber", num: "1", label: "DFM 待评审", trend: "结构改版待确认", up: true , ask: "B型号DFM评审进展如何？"},
    ],
    pm: [
      { icon: "clock", tone: "blue", num: "3", label: "在办项目", trend: "1 项里程碑临期", up: false , ask: "当前有哪些项目里程碑临期？", drill: { screen: "orders", kpi: "milestone_done_rate" }},
      { icon: "warn", tone: "red", num: "2", label: "项目风险项", trend: "1 项待升级协调", up: true , ask: "当前项目有哪些风险？"},
      { icon: "activity", tone: "green", num: "88%", label: "项目健康度", trend: "B 型号按计划", up: false , ask: "B型号项目健康度如何？"},
    ],
    te: [
      { icon: "check", tone: "blue", num: "6", label: "本周测试执行", trend: "全部留痕", up: false , ask: "B型号的测试执行情况如何？"},
      { icon: "warn", tone: "amber", num: "1", label: "可靠性不通过", trend: "高低温测试待复测", up: true , ask: "B型号可靠性测试结果如何？", drill: { screen: "quality", kpi: "test_pass_rate" }},
      { icon: "activity", tone: "green", num: "92%", label: "测试覆盖率", trend: "目标 ≥90%", up: false , ask: "B型号测试覆盖率是多少？"},
    ],
    me: [
      { icon: "clock", tone: "blue", num: "2", label: "结构变更在办", trend: "模具改版 1 项", up: false , ask: "B型号结构变更进展如何？"},
      { icon: "warn", tone: "amber", num: "1", label: "装配问题待分析", trend: "卡扣间隙超标", up: true , ask: "B型号装配间隙问题如何？", drill: { screen: "quality", kpi: "defect_rate" }},
      { icon: "activity", tone: "red", num: "1", label: "公差超差项", trend: "模具调整中", up: true , ask: "B型号公差配合是否合格？"},
    ],
    rdt: [
      { icon: "clock", tone: "blue", num: "4", label: "在办 Bug", trend: "1 项阻塞版本", up: true , ask: "当前有哪些阻塞版本的Bug？"},
      { icon: "check", tone: "green", num: "V1.8.2", label: "当前测试版本", trend: "回归中", up: false , ask: "当前测试版本回归情况如何？", drill: { screen: "quality", kpi: "test_pass_rate" }},
      { icon: "warn", tone: "amber", num: "2", label: "待判定遗留", trend: "版本放行前待确认", up: true , ask: "有哪些Bug遗留待判定？"},
    ],
    hr: [
      { icon: "check", tone: "blue", num: "3", label: "待办用章审批", trend: "全部留痕可追溯", up: false , ask: "有哪些用章申请待审批？", drill: { screen: "orders", kpi: "task_backlog_rate" }},
      { icon: "activity", tone: "amber", num: "2", label: "培训资质临期", trend: "1 项本月到期", up: true , ask: "哪些培训资质即将到期？"},
      { icon: "clock", tone: "green", num: "96%", label: "本月出勤率", trend: "持平", up: false , ask: "本月考勤情况如何？"},
    ],
    // ---------- 安全环保 / 客户服务 / 设备维保岗位看板（P1） ----------
    ehs: [
      { icon: "warn", tone: "red", num: "2", label: "安全隐患待整改", trend: "1 项临期", up: true , ask: "当前有哪些安全隐患待整改？", drill: { screen: "orders", kpi: "task_backlog_rate" }},
      { icon: "check", tone: "blue", num: "0", label: "本月安全事件", trend: "保持零事故", up: false , ask: "本月是否有安全事件？"},
      { icon: "activity", tone: "amber", num: "1", label: "危废转移待办", trend: "合规联单待归档", up: true , ask: "危废转移联单办理进度如何？"},
    ],
    cs: [
      { icon: "warn", tone: "red", num: "2", label: "待受理客诉", trend: "华为 S 级 1 件", up: true , ask: "当前有哪些客诉待受理？", drill: { screen: "quality", kpi: "complaint_close_rate" }},
      { icon: "clock", tone: "amber", num: "1.6h", label: "客诉平均响应", trend: "目标 ≤2h", up: false , ask: "客诉平均响应时间是多少？"},
      { icon: "check", tone: "green", num: "3", label: "售后工单在办", trend: "退换货 1 单", up: false , ask: "当前有哪些售后工单在办？"},
    ],
    mt: [
      { icon: "clock", tone: "blue", num: "4", label: "今日待执行点检", trend: "SMT 线体 2 项", up: false , ask: "今天有哪些设备点检任务？"},
      { icon: "warn", tone: "amber", num: "2", label: "维修工单在办", trend: "PRT-01 待更换刮刀", up: true , ask: "SMT-1现在设备参数异常吗？"},
      { icon: "check", tone: "green", num: "98%", label: "保养计划达成", trend: "本月按计划", up: false , ask: "本月设备保养计划达成率如何？", drill: { screen: "manufacturing", kpi: "availability" }},
    ],
  };

  const ALERT_SUMMARY = {
    gm:  { spc: 2, shortage: 3, order: 5, sla: 2 },
    mfg: { spc: 2, shortage: 1, order: 2, sla: 2 },
    pqe: { spc: 2, shortage: 0, order: 1, sla: 1 },
    mc:  { spc: 0, shortage: 3, order: 3, sla: 1 },
    biz: { spc: 0, shortage: 1, order: 5, sla: 1 },
    proc: { spc: 1, shortage: 2, order: 2, sla: 1 },
    wh:  { spc: 0, shortage: 1, order: 3, sla: 1 },
    rd:  { spc: 1, shortage: 0, order: 1, sla: 0 },
    eq:  { spc: 1, shortage: 0, order: 2, sla: 1 },
    pe:  { spc: 2, shortage: 0, order: 1, sla: 1 },
    pln: { spc: 0, shortage: 2, order: 4, sla: 1 },
    fin: { spc: 1, shortage: 1, order: 2, sla: 1 },
    qd:  { spc: 2, shortage: 1, order: 3, sla: 2 },
    eqm: { spc: 1, shortage: 0, order: 1, sla: 1 },
    rdm: { spc: 1, shortage: 0, order: 1, sla: 1 },
    scm: { spc: 0, shortage: 3, order: 4, sla: 1 },
    procm: { spc: 1, shortage: 2, order: 2, sla: 1 },
    whm: { spc: 0, shortage: 1, order: 3, sla: 1 },
    finm: { spc: 1, shortage: 1, order: 2, sla: 1 },
    ie:  { spc: 1, shortage: 0, order: 1, sla: 1 },
    npi: { spc: 1, shortage: 1, order: 1, sla: 0 },
    pm:  { spc: 0, shortage: 0, order: 2, sla: 1 },
    te:  { spc: 1, shortage: 0, order: 0, sla: 1 },
    me:  { spc: 1, shortage: 0, order: 1, sla: 1 },
    rdt: { spc: 0, shortage: 0, order: 1, sla: 1 },
    hr:  { spc: 0, shortage: 0, order: 1, sla: 0 },
    ehs: { spc: 1, shortage: 0, order: 0, sla: 1 },
    cs:  { spc: 1, shortage: 0, order: 2, sla: 1 },
    mt:  { spc: 1, shortage: 0, order: 1, sla: 1 },
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
      time: "18:12", owner: "PQE · 王倩", impact: "影响 A型号 SMT-1 当日产出约 6,800/10,000（达成 68%）；若 24h 内未闭环，B 型号转产计划将顺延，涉及华为终端 2 张临期订单。", steps: ["复测锡膏印刷参数（刮刀压力/速度），PQE 确认 CPK 回升", "加严 IPQC 频次至每 2h 一次，隔离近期 3 批次在制品", "与设备工程确认刮刀压力漂移根因，必要时更换刮刀", "闭环后更新 SPC 控制计划，复盘预防措施"], timeline: [["18:12", "SPC 系统触发 P1 预警", "自动"], ["18:13", "通知 PQE 王倩 / 制造李伟 / 设备组", "自动"], ["18:25", "PQE 首次响应：已启动参数复测", "王倩"], ["18:40", "联动设备工程介入排查刮刀", "李伟"]] },
    { id: "AL-2026-1028", type: "缺料", level: "P1", status: "处置中",
      title: "锡膏 M7020（千住金属）到货延迟 36h，库存可维持 2 天",
      detail: "原定今晨到货批次受物流影响延迟；若 48h 内未到货，SMT-1 将面临断料停线。采购已启动备用供应商询价（需采购总监确认 >5 万加急单）。",
      tags: ["供应链域 · 采购", "供应链域 · 物控", "制造域 · SMT车间"],
      time: "17:40", owner: "物控 · 赵敏", impact: "锡膏 M7020 库存仅可维持 2 天；SMT-1 连续生产依赖该物料，断料将触发停线（历史停线损失约 1.2 万/小时）。华为订单交付风险上升。", steps: ["确认备用供应商询价结果与到货周期", "评估替代锡膏（需 PQE 材料兼容性验证）", "若 48h 未到货：调整排产优先 B 型号订单", "同步业务侧评估对客交期影响"], timeline: [["17:40", "物控系统触发缺料 P1 预警", "自动"], ["17:41", "通知物控赵敏 / 采购孙立", "自动"], ["17:55", "采购已启动备用供应商询价", "孙立"], ["18:10", "物控发布库存消耗预警至制造群", "赵敏"]] },
    { id: "AL-2026-1027", type: "设备告警", level: "P2", status: "处置中",
      title: "PRT-01 印刷机刮刀压力 0.8MPa（基准 0.5MPa）",
      detail: "近 2 小时 3 条设备异常：刮刀压力偏高、钢网清洁周期 15min（基准 4h）、回流温区3 = 245℃（基准 235±5）。设备维护已现场处置。",
      tags: ["设备域 · 设备维护", "制造域 · SMT车间"],
      time: "16:58", owner: "设备 · 孙工", impact: "华为终端 S 级订单临期，延期将触发合同违约条款（日 0.05%）；该客户为本季度 Top1 客户，影响后续订单份额。", steps: ["核对成品库存与 ATP 可承诺量", "评估分批出货可行性（先交付已完工部分）", "若需延期：24h 内向客户给出书面口径", "升级销售总监决策（重大客户）"], timeline: [["16:20", "订单系统触发临期预警", "自动"], ["16:21", "通知业务陈皓 / 计划组", "自动"], ["16:35", "业务确认客户口径需求", "陈皓"]] },
    { id: "AL-2026-1026", type: "订单临期", level: "P1", status: "待处置",
      title: "SO-2026-10231（华为终端 · A型号）剩余 2 天，按当前 OEE 32.1% 无法按期交付",
      detail: "停线直接损失产出约 1,200pcs；需评估追产班次或与客户协商分批交付。对客 ETA 承诺须业务经理确认后方可输出。",
      tags: ["业务域 · 业务", "供应链域 · 物控", "制造域 · 生产计划"],
      time: "16:20", owner: "业务 · 陈皓", impact: "PRT-01 刮刀压力持续低于基准，直接影响印刷质量（与 CPK 越限预警关联）；若继续运行不良率将上升。", steps: ["设备工程现场复核压力传感器与刮刀磨损", "更换刮刀或校准传感器", "换件后首件确认 + 连续 3 批 SPC 监控", "更新设备维保记录"], timeline: [["15:50", "EAM 触发设备 P1 告警", "自动"], ["15:51", "通知设备组 / 制造现场", "自动"], ["16:05", "设备工程已接单到场", "设备组"]] },
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
        { k: "风险评级", v: "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线" },
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
        note: "初版内部简报（三域并行取证 + 风险评级 B）" },
    ],
  };

  /** 审批确认中心 · 演示待办（后端不可达时使用；SLA 倒计时按当前时间动态计算） */
  const DEMO_人工确认 = [
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

  /** 协同问数 · 后端离线时的演示回放（**从真实运行输出采集生成**，非手写） */
  const CHAT_DEMOS = {
    "A型号在SMT-1停线，何时复线？": {
      "events": [
        {
          "type": "agent_start",
          "node": "business",
          "label": "业务 · 问题识别"
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
          "label": "业务 · 问题识别",
          "summary": "识别场景：line_stop | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.6
        },
        {
          "type": "agent_start",
          "node": "confirm_stop",
          "label": "制造 · 确认停线"
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
          "label": "制造 · 确认停线",
          "summary": "SMT-1线A型号今日OEE=32.1%",
          "status": "ok",
          "elapsed_ms": 475.9
        },
        {
          "type": "agent_start",
          "node": "equipment",
          "label": "设备 · 查设备日志"
        },
        {
          "type": "agent_start",
          "node": "quality",
          "label": "品质 · 查质量数据"
        },
        {
          "type": "agent_start",
          "node": "supply_chain",
          "label": "供应链 · 查物料数据"
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
          "label": "供应链 · 查物料数据",
          "summary": "今日1个批次到货，无来料异常",
          "status": "ok",
          "elapsed_ms": 2.8
        },
        {
          "type": "agent_end",
          "node": "equipment",
          "label": "设备 · 查设备日志",
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
          "label": "品质 · 查质量数据",
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
          "label": "根因分析 · 案例匹配"
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
          "label": "根因分析 · 案例匹配",
          "summary": "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：分析服务未就绪，模板化结论）",
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
          "label": "报告 · 生成简报与对客报告"
        },
        {
          "type": "agent_end",
          "node": "report",
          "label": "报告 · 生成简报与对客报告",
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
          "风险评级": "评级B，根因：设备参数异常（刮刀压力=0.8MPa（基准0.5））与锡膏偏移不良相关，疑似工艺偏差，预计2h复线（degraded：分析服务未就绪，模板化结论）",
          "仓储": ""
        },
        "provenance": [
          {
            "channel": "registered",
            "query_name": "equip_alarms",
            "desc": "EAM：线体设备异常告警（等价 V1 Equipment）",
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
            "desc": "MES：指定线体/型号当日 OEE 与产出（等价 V1 Manufacturing）",
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
            "desc": "MES：进行中的停线事件（等价 V1 Manufacturing）",
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
            "desc": "QMS：不良分布聚合（等价 V1 Quality）",
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
            "desc": "SCM：今日到货来料批次（等价 V1 SupplyChain）",
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
              "body": "复线决策确认：是否按 FA 建议 ETA 复线？\n线体: SMT-1 | 型号: A型号\n风险评级: B | 预估复线: 2h\n审批人: 制造现场负责人",
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
              "body": "对客 ETA 承诺确认：确认回复客户的预计恢复时间？\n线体: SMT-1 | 型号: A型号\n风险评级: B | 预估复线: 2h\n审批人: 业务经理",
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
              "body": "停线影响评估工单\n线体: SMT-1 | 型号: A型号\n风险评级: B | 预估复线: 2h\n请评估排产/物料/对客影响（SLA 2h，超时自动升级）"
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
          "根因分析 分析服务未就绪：模板化结构化结论",
          "报告生成 分析服务未就绪：模板化 + 禁词表兜底"
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
          "label": "业务 · 问题识别"
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
          "label": "业务 · 问题识别",
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
          "label": "报告 · 来料异常简报"
        },
        {
          "type": "agent_end",
          "node": "report_incoming",
          "label": "报告 · 来料异常简报",
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
              "body": "退换货/特采决策确认\n线体: SMT-1 | 型号: A型号\n风险评级: ? | 预估复线: ?h\n审批人: SQE、PQE、生产",
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
          "报告生成 分析服务未就绪：模板化 + 禁词表兜底"
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
          "label": "业务 · 问题识别"
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
          "label": "业务 · 问题识别",
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
          "detail": "无 分析服务/无数据源 → 模板化结论（不编造）"
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
          "label": "报告 · 8D/对客报告"
        },
        {
          "type": "agent_end",
          "node": "report_complaint",
          "label": "报告 · 8D/对客报告",
          "summary": "内部简报已生成（6 项）；含数据缺口；对客报告（模板化）",
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
          "8D报告": "D1_team=质量牵头（QA）+ PQE + SQE + 工程 + 业务（客户接口）；D2_problem=华为终端 反馈：贴片元件偏移导致功能失效，不良率约2.3%（型号 A型号，批次 BATCH-A-1008）；D3_containment=在库/在途该批次冻结，已发货批次通知客户加严检验；D4_root_cause=过程不良以「贴片偏移」为主（46pcs），疑似贴装/工艺偏移；D5_permanent=工艺参数复核 + 首件全检 + 供应商来料加严（如需）；D6_validation=连续 3 批 CPK 达标且无同类不良后解除围堵；D7_prevent=更新 SOP/FMEA，纳入过程监控与供应商评级；D8_closure=待质量总监/QA 经理 人工确认 确认关闭",
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
            "note": "规则模板生成（无 分析服务/无数据源）"
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
            "desc": "TMS：型号发货单状态与 ETA（等价 V1 Warehouse）",
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
              "body": "8D 报告关闭确认\n线体: SMT-1 | 型号: A型号\n风险评级: ? | 预估复线: ?h\n审批人: 质量总监、QA经理",
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
              "body": "对客回复确认（脱敏后）\n线体: SMT-1 | 型号: A型号\n风险评级: ? | 预估复线: ?h\n审批人: 业务经理",
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
          "报告生成 分析服务未就绪：模板化 + 禁词表兜底"
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
          "label": "业务 · 问题识别"
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
          "label": "业务 · 问题识别",
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
          "label": "报告 · 出货风险简报"
        },
        {
          "type": "agent_end",
          "node": "report_delivery",
          "label": "报告 · 出货风险简报",
          "summary": "内部简报已生成（7 项）；对客报告（模板化）",
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
              "body": "对客交期承诺确认\n线体: SMT-1 | 型号: A型号\n风险评级: ? | 预估复线: ?h\n审批人: 业务经理",
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
          "报告生成 分析服务未就绪：模板化 + 禁词表兜底"
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
          "label": "业务 · 问题识别"
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
          "label": "业务 · 问题识别",
          "summary": "识别场景：equipment_warning | 型号：A型号 | 线体：SMT-1",
          "status": "ok",
          "elapsed_ms": 0.2
        },
        {
          "type": "agent_start",
          "node": "equipment_check",
          "label": "设备 · 查设备日志"
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
          "label": "设备 · 查设备日志",
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
          "detail": "无 分析服务/无数据源 → 模板化结论（不编造）"
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
          "detail": "无 分析服务/无数据源 → 模板化结论（不编造）"
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
          "label": "报告 · 设备简报"
        },
        {
          "type": "agent_end",
          "node": "report_equipment",
          "label": "报告 · 设备简报",
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
            "desc": "EAM：线体设备异常告警（等价 V1 Equipment）",
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
            "desc": "EAM：线体设备异常告警（等价 V1 Equipment）",
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
            "note": "规则模板生成（无 分析服务/无数据源）"
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
            "note": "规则模板生成（无 分析服务/无数据源）"
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
              "body": "停机窗口确认\n线体: SMT-1 | 型号: A型号\n风险评级: ? | 预估复线: ?h\n审批人: 制造现场负责人",
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
          "报告生成 分析服务未就绪：模板化 + 禁词表兜底"
        ],
        "fallback": null,
        "elapsed_ms": 123.2,
        "error": null
      }
    }
  };

  /** 向后兼容：默认回放 = line_stop 场景 */
  const CHAT_DEMO = CHAT_DEMOS["A型号在SMT-1停线，何时复线？"];

  /* 制造综合看板演示数据（与后端 /api/v2/dashboard/{manufacturing,quality}
     响应逐字段同构；后端离线 / graded 时前端降级渲染同一结构）。
     数值由后端聚合函数对 seed_v3 明细真实计算得出，口径与真实链路一致。 */
  const DASHBOARD = {
    "manufacturing": {
      "screen": "manufacturing",
      "generated_at": "2026-10-17 20:41:15",
      "source": "demo",
      "degraded": true,
      "degraded_reason": "后端离线 · 演示数据（口径与真实链路一致）",
      "window": { "start": "2026-10-08", "end": "2026-10-14", "days": 7, "label": "最近 7 天（演示窗口）" },
      "kpis": [
        { "key": "availability", "label": "稼动率", "value": 82.7, "unit": "%", "numerator": 3060, "denominator": 3700,
          "target": 90.0, "higher_is_better": true, "status": "warn", "delta": -7.3,
          "definition": "实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。", "formula": "3060 / 3700",
          "ask": "最近7天各产线稼动率如何？最低的是哪条线？",
          "trend": [91.7, 74.7, 91.7, 55.0, 91.2, 74.7, 97.9] },
        { "key": "scrap_rate", "label": "报废率", "value": 1.0, "unit": "%", "numerator": 108, "denominator": 10800,
          "target": 2.0, "higher_is_better": false, "status": "ok", "delta": -1.0,
          "definition": "报废数 ÷ 总产出数（不可返修的判废比例）。", "formula": "108 / 10800",
          "ask": "最近7天各产线报废率如何？最高的是哪条线？",
          "trend": [4.5, 3.0, 1.0, 0.7, 0.7, 0.5, 0.2] },
        { "key": "report_rate", "label": "报工完成率", "value": 81.8, "unit": "%", "numerator": 18, "denominator": 22,
          "target": 95.0, "higher_is_better": true, "status": "danger", "delta": -13.2,
          "definition": "已报工工序数 ÷ 应报工工序数。", "formula": "18 / 22",
          "ask": "最近7天哪些工单报工完成率偏低？",
          "trend": [100.0, 100.0, 100.0, 50.0, 66.7, 50.0, 50.0] },
        { "key": "test_fpy", "label": "测试直通率", "value": 90.9, "unit": "%", "numerator": 20, "denominator": 22,
          "target": 98.0, "higher_is_better": true, "status": "warn", "delta": -7.1,
          "definition": "首次测试通过数 ÷ 测试总数（重测才过不计入）。", "formula": "20 / 22",
          "ask": "最近7天各型号测试直通率如何？",
          "trend": [100.0, 100.0, 100.0, 100.0, 50.0, 75.0, 100.0] },
      ],
      "rankings": [
        { "key": "availability", "label": "稼动率", "unit": "%", "target": 90.0, "higher_is_better": true, "items": [
          { "name": "DIP-2 · B型号", "line": "DIP-2", "model": "B型号", "value": 55.0, "status": "danger", "numerator": 385, "denominator": 700, "rank": 1 },
          { "name": "SMT-2 · B型号", "line": "SMT-2", "model": "B型号", "value": 74.7, "status": "danger", "numerator": 448, "denominator": 600, "rank": 2 },
          { "name": "SMT-1 · A型号", "line": "SMT-1", "model": "A型号", "value": 91.5, "status": "ok", "numerator": 1317, "denominator": 1440, "rank": 3 },
          { "name": "DIP-1 · A型号", "line": "DIP-1", "model": "A型号", "value": 91.7, "status": "ok", "numerator": 440, "denominator": 480, "rank": 4 },
          { "name": "DIP-1 · C型号", "line": "DIP-1", "model": "C型号", "value": 97.9, "status": "ok", "numerator": 470, "denominator": 480, "rank": 5 }
        ] },
        { "key": "scrap_rate", "label": "报废率", "unit": "%", "target": 2.0, "higher_is_better": false, "items": [
          { "name": "DIP-2 · B型号", "line": "DIP-2", "model": "B型号", "value": 4.5, "status": "danger", "numerator": 45, "denominator": 1000, "rank": 1 },
          { "name": "SMT-2 · C型号", "line": "SMT-2", "model": "C型号", "value": 3.0, "status": "warn", "numerator": 15, "denominator": 500, "rank": 2 },
          { "name": "SMT-1 · A型号", "line": "SMT-1", "model": "A型号", "value": 1.0, "status": "ok", "numerator": 20, "denominator": 2000, "rank": 3 },
          { "name": "DIP-1 · C型号", "line": "DIP-1", "model": "C型号", "value": 0.7, "status": "ok", "numerator": 10, "denominator": 1500, "rank": 4 },
          { "name": "SMT-2 · B型号", "line": "SMT-2", "model": "B型号", "value": 0.5, "status": "ok", "numerator": 10, "denominator": 2000, "rank": 5 }
        ] },
        { "key": "report_rate", "label": "报工完成率", "unit": "%", "target": 95.0, "higher_is_better": true, "items": [
          { "name": "WO-B-1014", "wo": "WO-B-1014", "line": "DIP-2", "model": "B型号", "value": 60.0, "status": "danger", "numerator": 3, "denominator": 5, "rank": 1 },
          { "name": "WO-C-1011", "wo": "WO-C-1011", "line": "SMT-2", "model": "C型号", "value": 66.7, "status": "danger", "numerator": 2, "denominator": 3, "rank": 2 },
          { "name": "WO-B-1013", "wo": "WO-B-1013", "line": "DIP-2", "model": "B型号", "value": 80.0, "status": "danger", "numerator": 4, "denominator": 5, "rank": 3 },
          { "name": "WO-A-1014", "wo": "WO-A-1014", "line": "SMT-1", "model": "A型号", "value": 100.0, "status": "ok", "numerator": 4, "denominator": 4, "rank": 4 },
          { "name": "WO-C-1013", "wo": "WO-C-1013", "line": "DIP-1", "model": "C型号", "value": 100.0, "status": "ok", "numerator": 5, "denominator": 5, "rank": 5 }
        ] },
        { "key": "test_fpy", "label": "测试直通率", "unit": "%", "target": 98.0, "higher_is_better": true, "items": [
          { "name": "C型号", "line": "", "model": "C型号", "value": 83.3, "status": "danger", "numerator": 5, "denominator": 6, "rank": 1 },
          { "name": "B型号", "line": "", "model": "B型号", "value": 92.3, "status": "warn", "numerator": 12, "denominator": 13, "rank": 2 },
          { "name": "A型号", "line": "", "model": "A型号", "value": 100.0, "status": "ok", "numerator": 3, "denominator": 3, "rank": 3 }
        ] },
      ],
      "notes": [
        "稼动率 = 实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
        "报废率 = 报废数 ÷ 总产出数；返工品既不计入良品也不计入报废。",
        "报工完成率 = 已报工工序数 ÷ 应报工工序数。",
        "测试直通率 = 首次测试通过数 ÷ 测试总数（重测才过不计入）。",
        "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转：越高越好取最低 5、越低越好取最高 5。"
      ]
    },
    "quality": {
      "screen": "quality",
      "generated_at": "2026-10-17 20:41:15",
      "source": "demo",
      "degraded": true,
      "degraded_reason": "后端离线 · 演示数据（口径与真实链路一致）",
      "window": { "start": "2026-10-08", "end": "2026-10-14", "days": 7, "label": "最近 7 天（演示窗口）" },
      "kpis": [
        { "key": "defect_rate", "label": "不良率", "value": 2.1, "unit": "%", "numerator": 269, "denominator": 12800,
          "target": 2.0, "higher_is_better": false, "status": "warn", "delta": 0.1,
          "definition": "不良数 ÷ 受检总数。", "formula": "269 / 12800",
          "ask": "最近7天各产线不良率如何？最高的是哪条线？",
          "trend": [1.9, 3.5, 2.8, 2.9, 2.2, 1.1, 1.3] },
        { "key": "fpy", "label": "直通率 FPY", "value": 97.9, "unit": "%", "numerator": 12531, "denominator": 12800,
          "target": 98.0, "higher_is_better": true, "status": "warn", "delta": -0.1,
          "definition": "一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。", "formula": "12531 / 12800",
          "ask": "最近7天各产线直通率如何？最低的是哪条线？",
          "trend": [98.1, 96.5, 97.2, 97.1, 97.8, 98.9, 98.7] },
      ],
      "rankings": [
        { "key": "defect_rate", "label": "不良率", "unit": "%", "target": 2.0, "higher_is_better": false, "items": [
          { "name": "DIP-2 · B型号", "line": "DIP-2", "model": "B型号", "value": 3.5, "status": "danger", "numerator": 42, "denominator": 1200, "rank": 1 },
          { "name": "SMT-2 · C型号", "line": "SMT-2", "model": "C型号", "value": 2.9, "status": "warn", "numerator": 26, "denominator": 900, "rank": 2 },
          { "name": "SMT-2 · A型号", "line": "SMT-2", "model": "A型号", "value": 2.3, "status": "warn", "numerator": 46, "denominator": 2000, "rank": 3 },
          { "name": "SMT-2 · B型号", "line": "SMT-2", "model": "B型号", "value": 2.1, "status": "warn", "numerator": 62, "denominator": 3000, "rank": 4 },
          { "name": "DIP-1 · C型号", "line": "DIP-1", "model": "C型号", "value": 1.9, "status": "ok", "numerator": 17, "denominator": 900, "rank": 5 }
        ] },
        { "key": "fpy", "label": "直通率", "unit": "%", "target": 98.0, "higher_is_better": true, "items": [
          { "name": "DIP-2 · B型号", "line": "DIP-2", "model": "B型号", "value": 96.5, "status": "warn", "numerator": 1158, "denominator": 1200, "rank": 1 },
          { "name": "SMT-2 · C型号", "line": "SMT-2", "model": "C型号", "value": 97.1, "status": "warn", "numerator": 874, "denominator": 900, "rank": 2 },
          { "name": "SMT-2 · A型号", "line": "SMT-2", "model": "A型号", "value": 97.7, "status": "warn", "numerator": 1954, "denominator": 2000, "rank": 3 },
          { "name": "SMT-2 · B型号", "line": "SMT-2", "model": "B型号", "value": 97.9, "status": "warn", "numerator": 2938, "denominator": 3000, "rank": 4 },
          { "name": "DIP-1 · C型号", "line": "DIP-1", "model": "C型号", "value": 98.1, "status": "ok", "numerator": 883, "denominator": 900, "rank": 5 }
        ] },
      ],
      "pareto": [
        { "name": "锡膏偏移", "value": 77, "share": 28.6, "cum_share": 28.6 },
        { "name": "贴片偏移", "value": 46, "share": 17.1, "cum_share": 45.7 },
        { "name": "虚焊", "value": 42, "share": 15.6, "cum_share": 61.3 },
        { "name": "元件缺失", "value": 38, "share": 14.1, "cum_share": 75.4 },
        { "name": "极性反", "value": 33, "share": 12.3, "cum_share": 87.7 },
        { "name": "其他", "value": 33, "share": 12.3, "cum_share": 100.0 }
      ],
      "pareto_label": "不良类型",
      "pareto_kpi": "defect_rate",
      "pareto_vital": ["锡膏偏移", "贴片偏移", "虚焊", "元件缺失", "极性反"],
      "pareto_threshold": 80.0,
      "notes": [
        "不良率 = 不良数 ÷ 受检总数。",
        "直通率 FPY = 一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
        "标准三率 = 标准完整率 × 标准完善率 × 标准遵守率 = 符合率（完整率：该有的标准是否齐全；完善率：标准与现场是否一致；遵守率：现场是否按标准作业）。",
        "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转：越高越好取最低 5、越低越好取最高 5。",
        "累计不良柏拉图：窗口内按不良类型降序累积，关键少数 = 累计占比首达 80% 的类型（与 SPC 柏拉图 spc.pareto 同源口径）；点击类型可下钻其产线 · 型号分解。"
      ],
      "standard_three_rates": {
        "definition": "标准三率 = 标准完整率 × 标准完善率 × 标准遵守率 = 符合率",
        "targets": { "completeness_rate": 100.0, "perfection_rate": 98.0, "compliance_rate": 90.0 },
        "overall": { "completeness_rate": 80.0, "perfection_rate": 75.0, "compliance_rate": 81.0,
          "conformity_rate": 48.6, "status": "danger", "formula": "80.0% × 75.0% × 81.0% = 48.6%" },
        "by_model": [
          { "model": "A型号", "completeness_rate": 100.0, "perfection_rate": 66.7, "compliance_rate": 90.0, "conformity_rate": 60.0, "status": "danger" },
          { "model": "B型号", "completeness_rate": 66.7, "perfection_rate": 50.0, "compliance_rate": 73.3, "conformity_rate": 24.4, "status": "danger" },
          { "model": "C型号", "completeness_rate": 75.0, "perfection_rate": 100.0, "compliance_rate": 80.0, "conformity_rate": 60.0, "status": "danger" }
        ]
      }
    }
  };

  /* 订单履约看板演示数据（与后端 /api/v2/dashboard/orders 响应逐字段同构）。
     口径与真实链路一致（seed_v3 明细预聚合）：OTD 20/28、OTIF 19/28、
     延期率 8/28、齐套率 259/280；TOP5 为「最需关注的最差 5 名」。 */
  const ORDERS_DASHBOARD = {
    "screen": "orders",
    "generated_at": "2026-10-18 09:20:00",
    "source": "demo",
    "degraded": true,
    "degraded_reason": "后端离线 · 演示数据（口径与真实链路一致）",
    "window": { "start": "2026-10-08", "end": "2026-10-14", "days": 7, "label": "最近 7 天（演示窗口）" },
    "kpis": [
      { "key": "otd", "label": "OTD 准时交付率", "value": 71.4, "unit": "%", "numerator": 20, "denominator": 28,
        "target": 98.0, "higher_is_better": true, "status": "danger", "delta": -26.6,
        "definition": "准时交付（实际发货日 ≤ 客户要求交付日）订单数 ÷ 订单总数。", "formula": "20 / 28",
        "ask": "最近7天各客户 OTD 准时交付率如何？最低的是哪个客户？",
        "trend": [100.0, 75.0, 33.3, 50.0, 80.0, 75.0, 75.0] },
      { "key": "otif", "label": "OTIF 准时足额率", "value": 67.9, "unit": "%", "numerator": 19, "denominator": 28,
        "target": 95.0, "higher_is_better": true, "status": "danger", "delta": -27.1,
        "definition": "同时满足准时且足额的订单数 ÷ 订单总数。", "formula": "19 / 28",
        "ask": "最近7天各客户 OTIF 准时足额率如何？最低的是哪个客户？",
        "trend": [100.0, 75.0, 33.3, 50.0, 80.0, 75.0, 50.0] },
      { "key": "delay_rate", "label": "延期订单率", "value": 28.6, "unit": "%", "numerator": 8, "denominator": 28,
        "target": 5.0, "higher_is_better": false, "status": "danger", "delta": 23.6,
        "definition": "延期天数 > 0 的订单数 ÷ 订单总数（越低越好）。", "formula": "8 / 28",
        "ask": "最近7天各客户延期订单率如何？最高的是哪个客户？延期原因有哪些？",
        "trend": [0.0, 25.0, 66.7, 50.0, 20.0, 25.0, 25.0] },
      { "key": "kit_rate", "label": "齐套率", "value": 93.7, "unit": "%", "numerator": 251, "denominator": 268,
        "target": 95.0, "higher_is_better": true, "status": "warn", "delta": -1.3,
        "definition": "已齐套料数 ÷ 应齐套料数。", "formula": "251 / 268",
        "ask": "最近7天各型号订单齐套率如何？最低的是哪个型号？缺料集中在哪些料号？",
        "trend": [100.0, 89.5, 88.2, 89.5, 96.3, 94.1, 97.1] },
    ],
    "rankings": [
      { "key": "otd", "label": "OTD 准时交付率", "unit": "%", "target": 98.0, "higher_is_better": true, "items": [
        { "name": "华为终端 · S", "entity": { "customer": "华为终端", "level": "S" }, "line": "", "model": "", "wo": "", "value": 33.3, "status": "danger", "numerator": 2, "denominator": 6, "rank": 1 },
        { "name": "OPPO · B", "entity": { "customer": "OPPO", "level": "B" }, "line": "", "model": "", "wo": "", "value": 66.7, "status": "danger", "numerator": 2, "denominator": 3, "rank": 2 },
        { "name": "小米 · A", "entity": { "customer": "小米", "level": "A" }, "line": "", "model": "", "wo": "", "value": 75.0, "status": "danger", "numerator": 3, "denominator": 4, "rank": 3 },
        { "name": "比亚迪 · A", "entity": { "customer": "比亚迪", "level": "A" }, "line": "", "model": "", "wo": "", "value": 75.0, "status": "danger", "numerator": 3, "denominator": 4, "rank": 4 },
        { "name": "联想 · B", "entity": { "customer": "联想", "level": "B" }, "line": "", "model": "", "wo": "", "value": 75.0, "status": "danger", "numerator": 3, "denominator": 4, "rank": 5 },
      ] },
      { "key": "otif", "label": "OTIF 准时足额率", "unit": "%", "target": 95.0, "higher_is_better": true, "items": [
        { "name": "华为终端 · S", "entity": { "customer": "华为终端", "level": "S" }, "line": "", "model": "", "wo": "", "value": 33.3, "status": "danger", "numerator": 2, "denominator": 6, "rank": 1 },
        { "name": "联想 · B", "entity": { "customer": "联想", "level": "B" }, "line": "", "model": "", "wo": "", "value": 50.0, "status": "danger", "numerator": 2, "denominator": 4, "rank": 2 },
        { "name": "OPPO · B", "entity": { "customer": "OPPO", "level": "B" }, "line": "", "model": "", "wo": "", "value": 66.7, "status": "danger", "numerator": 2, "denominator": 3, "rank": 3 },
        { "name": "小米 · A", "entity": { "customer": "小米", "level": "A" }, "line": "", "model": "", "wo": "", "value": 75.0, "status": "danger", "numerator": 3, "denominator": 4, "rank": 4 },
        { "name": "比亚迪 · A", "entity": { "customer": "比亚迪", "level": "A" }, "line": "", "model": "", "wo": "", "value": 75.0, "status": "danger", "numerator": 3, "denominator": 4, "rank": 5 },
      ] },
      { "key": "delay_rate", "label": "延期订单率", "unit": "%", "target": 5.0, "higher_is_better": false, "items": [
        { "name": "华为终端 · S", "entity": { "customer": "华为终端", "level": "S" }, "line": "", "model": "", "wo": "", "value": 66.7, "status": "danger", "numerator": 4, "denominator": 6, "rank": 1 },
        { "name": "OPPO · B", "entity": { "customer": "OPPO", "level": "B" }, "line": "", "model": "", "wo": "", "value": 33.3, "status": "danger", "numerator": 1, "denominator": 3, "rank": 2 },
        { "name": "小米 · A", "entity": { "customer": "小米", "level": "A" }, "line": "", "model": "", "wo": "", "value": 25.0, "status": "danger", "numerator": 1, "denominator": 4, "rank": 3 },
        { "name": "比亚迪 · A", "entity": { "customer": "比亚迪", "level": "A" }, "line": "", "model": "", "wo": "", "value": 25.0, "status": "danger", "numerator": 1, "denominator": 4, "rank": 4 },
        { "name": "联想 · B", "entity": { "customer": "联想", "level": "B" }, "line": "", "model": "", "wo": "", "value": 25.0, "status": "danger", "numerator": 1, "denominator": 4, "rank": 5 },
      ] },
      { "key": "kit_rate", "label": "齐套率", "unit": "%", "target": 95.0, "higher_is_better": true, "items": [
        { "name": "A型号", "entity": { "model": "A型号" }, "line": "", "model": "A型号", "wo": "", "value": 92.2, "status": "warn", "numerator": 94, "denominator": 102, "rank": 1 },
        { "name": "B型号", "entity": { "model": "B型号" }, "line": "", "model": "B型号", "wo": "", "value": 94.0, "status": "warn", "numerator": 94, "denominator": 100, "rank": 2 },
        { "name": "C型号", "entity": { "model": "C型号" }, "line": "", "model": "C型号", "wo": "", "value": 95.5, "status": "ok", "numerator": 63, "denominator": 66, "rank": 3 },
      ] },
    ],
    "notes": [
      "OTD 准时交付率 = 实际发货日 ≤ 客户要求交付日 的订单数 ÷ 窗口内订单总数（订单按客户要求交付日 due_date 归入统计窗口）。",
      "OTIF 准时足额率 = 同时满足「准时」与「足额（齐套发齐）」的订单数 ÷ 订单总数，比 OTD 更严格，是客户面向的核心履约指标。",
      "延期订单率 = 预计/实际延期天数 > 0 的订单数 ÷ 订单总数（越低越好）。",
      "齐套率 = 已齐套料数 ÷ 应齐套料数（物料齐套对按期交付的保障度）。",
      "TOP5 统一为「最需关注的最差 5 名」，方向随指标极性自动翻转：越高越好取最低 5、越低越好取最高 5。",
    ],
    "pipeline": [
      { "stage": "已下单", "count": 0, "delayed": 0 },
      { "stage": "计划", "count": 1, "delayed": 1 },
      { "stage": "采购", "count": 2, "delayed": 2 },
      { "stage": "生产", "count": 3, "delayed": 3 },
      { "stage": "质检", "count": 1, "delayed": 1 },
      { "stage": "发货", "count": 21, "delayed": 1 },
    ],
    "risk_orders": [
      { "order_no": "SO-2026-10247", "customer": "华为终端", "customer_level": "S", "product_model": "A型号", "qty": 12000, "stage": "质检", "risk_type": "客诉", "delay_days": 4, "progress": 45, "shortage_note": "PCB 批次 BATCH-A-1008（深南电路）客诉待 8D 闭环", "owner": "业务·张伟" },
      { "order_no": "SO-2026-10218", "customer": "华为终端", "customer_level": "S", "product_model": "B型号", "qty": 6000, "stage": "采购", "risk_type": "缺料", "delay_days": 3, "progress": 30, "shortage_note": "主控芯片 IC-100 交期顺延", "owner": "业务·张伟" },
      { "order_no": "SO-2026-10205", "customer": "联想", "customer_level": "B", "product_model": "C型号", "qty": 6000, "stage": "采购", "risk_type": "缺料", "delay_days": 3, "progress": 38, "shortage_note": "屏蔽罩齐套率 78%，采购在途", "owner": "业务·王强" },
      { "order_no": "SO-2026-10231", "customer": "华为终端", "customer_level": "S", "product_model": "A型号", "qty": 8000, "stage": "生产", "risk_type": "缺料", "delay_days": 2, "progress": 62, "shortage_note": "锡膏 M7020（千住金属）到货延迟 36h", "owner": "业务·张伟" },
      { "order_no": "SO-2026-10230", "customer": "小米", "customer_level": "A", "product_model": "B型号", "qty": 3000, "stage": "发货", "risk_type": "延期风险", "delay_days": 2, "progress": 100, "shortage_note": "", "owner": "业务·李娜" },
      { "order_no": "SO-2026-10236", "customer": "OPPO", "customer_level": "B", "product_model": "B型号", "qty": 4000, "stage": "生产", "risk_type": "缺料", "delay_days": 2, "progress": 60, "shortage_note": "元件立碑频发，暂停投料", "owner": "业务·周敏" },
      { "order_no": "SO-2026-10186", "customer": "比亚迪", "customer_level": "A", "product_model": "A型号", "qty": 5000, "stage": "生产", "risk_type": "停线", "delay_days": 1, "progress": 81, "shortage_note": "受 SMT-1 停线牵连，复线后追产", "owner": "业务·陈刚" },
      { "order_no": "SO-2026-10255", "customer": "华为终端", "customer_level": "S", "product_model": "A型号", "qty": 5000, "stage": "计划", "risk_type": "延期风险", "delay_days": 1, "progress": 20, "shortage_note": "", "owner": "业务·张伟" },
    ],
  };
  // 订单看板复用同一套下钻生成器：DASHBOARD.drill.orders 由 DRILL_META.orders 生成
  DASHBOARD.orders = ORDERS_DASHBOARD;

  /* SPC 过程能力看板演示数据（与后端 /api/v2/dashboard/spc 响应逐字段同构）。
     口径与真实链路一致（core/spc 双西格玛内核 + seed_v3 明细预聚合）：
     焊点高度 Cpk=1.461/合格、锡膏厚度 1.146/偏低、回流焊峰值温度 0.615/不合格；
     不良柏拉图与 spc.pareto 同源（关键少数 = 累计首达 80% → 前 5 项）。 */
  const SPC_DASHBOARD = {
    "screen": "spc",
    "generated_at": "2026-10-18 09:20:00",
    "source": "demo",
    "degraded": true,
    "degraded_reason": "后端离线 · 演示数据（口径与真实链路一致）",
    "window": { "start": "2026-10-08", "end": "2026-10-14", "days": 7, "label": "最近 7 天（演示窗口）" },
    "kpis": [
      { "key": "spc_capability", "characteristic": "焊点高度(mm)", "label": "焊点高度(mm) 过程能力",
        "value": 1.461, "unit": "", "cp": 1.520, "cpk": 1.461, "pp": 1.421, "ppk": 1.396,
        "sigma_within": 0.033, "sigma_overall": 0.035, "n_total": 100, "n_subgroups": 20,
        "spec": { "usl": 0.9, "lsl": 0.6 }, "verdict": "合格", "status": "ok",
        "target": 1.33, "higher_is_better": true, "delta": 0.13, "data_gap": false, "warnings": [],
        "over_rate": 0.0, "over_num": 0, "over_den": 100,
        "definition": "双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R）；Pp/Ppk 用整体 σ。判定线：≥1.33 合格 / ≥1.00 偏低 / <1.00 不合格。",
        "formula": "Cpk=1.461",
        "ask": "焊点高度(mm) 的过程能力 Cpk 如何？是否需要加严检验或工程介入？",
        "entity": { "characteristic": "焊点高度(mm)" }, "trend": [] },
      { "key": "spc_capability", "characteristic": "锡膏厚度(μm)", "label": "锡膏厚度(μm) 过程能力",
        "value": 1.146, "unit": "", "cp": 1.149, "cpk": 1.146, "pp": 1.10, "ppk": 1.05,
        "sigma_within": 5.8, "sigma_overall": 6.1, "n_total": 100, "n_subgroups": 20,
        "spec": { "usl": 160, "lsl": 120 }, "verdict": "偏低", "status": "warn",
        "target": 1.33, "higher_is_better": true, "delta": -0.18, "data_gap": false, "warnings": [],
        "over_rate": 3.0, "over_num": 3, "over_den": 100,
        "definition": "双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R）；Pp/Ppk 用整体 σ。判定线：≥1.33 合格 / ≥1.00 偏低 / <1.00 不合格。",
        "formula": "Cpk=1.146",
        "ask": "锡膏厚度(μm) 的过程能力 Cpk 如何？是否需要加严检验或工程介入？",
        "entity": { "characteristic": "锡膏厚度(μm)" }, "trend": [] },
      { "key": "spc_capability", "characteristic": "回流焊峰值温度(℃)", "label": "回流焊峰值温度(℃) 过程能力",
        "value": 0.615, "unit": "", "cp": 0.625, "cpk": 0.615, "pp": 0.58, "ppk": 0.55,
        "sigma_within": 4.0, "sigma_overall": 4.3, "n_total": 100, "n_subgroups": 20,
        "spec": { "usl": 250, "lsl": 235 }, "verdict": "不合格", "status": "danger",
        "target": 1.33, "higher_is_better": true, "delta": -0.72, "data_gap": false, "warnings": [],
        "over_rate": 8.0, "over_num": 8, "over_den": 100,
        "definition": "双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R）；Pp/Ppk 用整体 σ。判定线：≥1.33 合格 / ≥1.00 偏低 / <1.00 不合格。",
        "formula": "Cpk=0.615",
        "ask": "回流焊峰值温度(℃) 的过程能力 Cpk 如何？是否需要加严检验或工程介入？",
        "entity": { "characteristic": "回流焊峰值温度(℃)" }, "trend": [] },
    ],
    "capabilities": [
      { "characteristic": "焊点高度(mm)", "verdict": "合格", "data_gap": false, "warnings": [],
        "cp": 1.520, "cpk": 1.461, "pp": 1.421, "ppk": 1.396,
        "sigma_within": 0.033, "sigma_overall": 0.035, "n_total": 100, "n_subgroups": 20,
        "spec": { "usl": 0.9, "lsl": 0.6 } },
      { "characteristic": "锡膏厚度(μm)", "verdict": "偏低", "data_gap": false, "warnings": [],
        "cp": 1.149, "cpk": 1.146, "pp": 1.10, "ppk": 1.05,
        "sigma_within": 5.8, "sigma_overall": 6.1, "n_total": 100, "n_subgroups": 20,
        "spec": { "usl": 160, "lsl": 120 } },
      { "characteristic": "回流焊峰值温度(℃)", "verdict": "不合格", "data_gap": false, "warnings": [],
        "cp": 0.625, "cpk": 0.615, "pp": 0.58, "ppk": 0.55,
        "sigma_within": 4.0, "sigma_overall": 4.3, "n_total": 100, "n_subgroups": 20,
        "spec": { "usl": 250, "lsl": 235 } },
    ],
    "pareto": [
      { "name": "元件立碑", "value": 120, "share": 29.6, "cum_share": 29.6, "status": "danger",
        "entity": { "defect_type": "元件立碑" }, "ask": "元件立碑 不良 120 件占比 29.6%，如何降低该类缺陷？" },
      { "name": "锡珠", "value": 86, "share": 21.2, "cum_share": 50.8, "status": "warn",
        "entity": { "defect_type": "锡珠" }, "ask": "锡珠 不良 86 件占比 21.2%，如何降低该类缺陷？" },
      { "name": "连锡", "value": 62, "share": 15.3, "cum_share": 66.1, "status": "warn",
        "entity": { "defect_type": "连锡" }, "ask": "连锡 不良 62 件占比 15.3%，如何降低该类缺陷？" },
      { "name": "少锡/虚焊", "value": 45, "share": 11.1, "cum_share": 77.2, "status": "warn",
        "entity": { "defect_type": "少锡/虚焊" }, "ask": "少锡/虚焊 不良 45 件占比 11.1%，如何降低该类缺陷？" },
      { "name": "元件偏移", "value": 32, "share": 7.9, "cum_share": 85.1, "status": "warn",
        "entity": { "defect_type": "元件偏移" }, "ask": "元件偏移 不良 32 件占比 7.9%，如何降低该类缺陷？" },
      { "name": "极性反", "value": 24, "share": 5.9, "cum_share": 91.0, "status": "ok",
        "entity": { "defect_type": "极性反" }, "ask": "极性反 不良 24 件占比 5.9%，如何降低该类缺陷？" },
      { "name": "外观划伤", "value": 16, "share": 3.9, "cum_share": 94.9, "status": "ok",
        "entity": { "defect_type": "外观划伤" }, "ask": "外观划伤 不良 16 件占比 3.9%，如何降低该类缺陷？" },
      { "name": "元件缺失", "value": 11, "share": 2.7, "cum_share": 97.6, "status": "ok",
        "entity": { "defect_type": "元件缺失" }, "ask": "元件缺失 不良 11 件占比 2.7%，如何降低该类缺陷？" },
      { "name": "锡膏偏移", "value": 7, "share": 1.7, "cum_share": 99.3, "status": "ok",
        "entity": { "defect_type": "锡膏偏移" }, "ask": "锡膏偏移 不良 7 件占比 1.7%，如何降低该类缺陷？" },
      { "name": "其他", "value": 3, "share": 0.7, "cum_share": 100.0, "status": "ok",
        "entity": { "defect_type": "其他" }, "ask": "其他 不良 3 件占比 0.7%，如何降低该类缺陷？" },
    ],
    "rankings": [
      { "key": "spc_capability", "label": "子组超差率", "unit": "%", "target": 5.0, "higher_is_better": false, "items": [
        { "name": "回流焊峰值温度(℃)", "entity": { "characteristic": "回流焊峰值温度(℃)" }, "line": "", "model": "", "wo": "", "value": 8.0, "status": "danger", "numerator": 8, "denominator": 100, "rank": 1 },
        { "name": "锡膏厚度(μm)", "entity": { "characteristic": "锡膏厚度(μm)" }, "line": "", "model": "", "wo": "", "value": 3.0, "status": "ok", "numerator": 3, "denominator": 100, "rank": 2 },
        { "name": "焊点高度(mm)", "entity": { "characteristic": "焊点高度(mm)" }, "line": "", "model": "", "wo": "", "value": 0.0, "status": "ok", "numerator": 0, "denominator": 100, "rank": 3 },
      ] },
      { "key": "spc_pareto", "label": "缺陷类型不良率", "unit": "%", "target": 10.0, "higher_is_better": false, "items": [
        { "name": "元件立碑", "entity": { "defect_type": "元件立碑" }, "line": "", "model": "", "wo": "", "value": 18.0, "status": "danger", "numerator": 36, "denominator": 200, "rank": 1 },
        { "name": "连锡", "entity": { "defect_type": "连锡" }, "line": "", "model": "", "wo": "", "value": 13.0, "status": "warn", "numerator": 26, "denominator": 200, "rank": 2 },
        { "name": "少锡/虚焊", "entity": { "defect_type": "少锡/虚焊" }, "line": "", "model": "", "wo": "", "value": 6.0, "status": "ok", "numerator": 12, "denominator": 200, "rank": 3 },
        { "name": "锡珠", "entity": { "defect_type": "锡珠" }, "line": "", "model": "", "wo": "", "value": 4.5, "status": "ok", "numerator": 9, "denominator": 200, "rank": 4 },
        { "name": "元件偏移", "entity": { "defect_type": "元件偏移" }, "line": "", "model": "", "wo": "", "value": 3.0, "status": "ok", "numerator": 6, "denominator": 200, "rank": 5 },
      ] },
    ],
    "notes": [
      "过程能力指数双西格玛口径：Cp/Cpk 用组内 σ（Xbar-R，R̄/d2）；Pp/Ppk 用整体 σ（样本标准差 ddof=1），保证 Pp ≠ Cp。",
      "判定线：Cpk ≥ 1.33 合格；1.00 ≤ Cpk < 1.33 偏低；Cpk < 1.00 不合格。",
      "L1 卡片展示 Cpk（无量纲指数，由确定性内核 core/spc 计算）；下钻采用「子组超差率」，以复用统一聚合引擎（Cpk 与超差率是同一过程的两面）。",
      "不良柏拉图与 SPC 工具 spc.pareto 同源（quality_defect 全量聚合，关键少数 = 累计首达 80%）。",
      "缺测量值 / 缺规格限时该特性标数据缺口（data_gap），绝不编造指数。",
    ],
    "data_gap": false,
  };
  DASHBOARD.spc = SPC_DASHBOARD;

  /* ---------------- 看板下钻（L2/L3）离线降级数据 ----------------
     直接镜像上面的 rankings 分组行（离线仅含 TOP5 子集，故标注 degraded）：
     真实链路下 L2 是该指标「按维度全量分解」，L3 是单实体逐日 + 原始记录。 */
  // SPC 下钻离线模板：L3「关联原始记录」列（与后端 record_cols / _COL_CN 一致）
  const _SPC_MEAS_COLS = [
    { key: "measured_at", label: "测量时间" }, { key: "characteristic", label: "特性" },
    { key: "subgroup_no", label: "子组号" }, { key: "seq", label: "序号" },
    { key: "value", label: "测量值" }, { key: "usl", label: "规格上限" },
    { key: "lsl", label: "规格下限" }, { key: "source", label: "数据来源" },
  ];
  const _SPC_DEFECT_COLS = [
    { key: "inspect_time", label: "检验时间" }, { key: "batch_no", label: "批次" },
    { key: "product_model", label: "型号" }, { key: "line_code", label: "产线" },
    { key: "defect_type", label: "不良类型" }, { key: "defect_qty", label: "不良数" },
    { key: "total_qty", label: "受检总数" },
  ];
  const DRILL_META = {
    manufacturing: {
      availability: { label: "稼动率", target: 90.0, higher_is_better: true, dimension: "产线 · 型号",
        definition: "实际运行分钟 ÷ 计划运行分钟（分母已排除计划停机）。",
        ask: "最近7天各产线稼动率如何？最低的是哪条线？" },
      scrap_rate: { label: "报废率", target: 2.0, higher_is_better: false, dimension: "产线 · 型号",
        definition: "报废数 ÷ 总产出数（不可返修的判废比例）。",
        ask: "最近7天各产线报废率如何？最高的是哪条线？" },
      report_rate: { label: "报工完成率", target: 95.0, higher_is_better: true, dimension: "工单号",
        definition: "已报工工序数 ÷ 应报工工序数。",
        ask: "最近7天哪些工单报工完成率偏低？" },
      test_fpy: { label: "测试直通率", target: 98.0, higher_is_better: true, dimension: "型号",
        definition: "首次测试通过数 ÷ 测试总数（重测才过不计入）。",
        ask: "最近7天各型号测试直通率如何？" },
      oee: { label: "设备综合效率 OEE", target: 85.0, higher_is_better: true, dimension: "产线 · 型号",
        definition: "OEE 按记录平均（production_output.oee 各记录均值）。",
        ask: "最近7天各产线 OEE 如何？最低的是哪条线？",
        rows: [
          { name: "SMT-2 · B型号", entity: { line: "SMT-2", model: "B型号" }, value: 78.8, numerator: 1.6, denominator: 2, status: "warn", trend: [] },
          { name: "SMT-1 · A型号", entity: { line: "SMT-1", model: "A型号" }, value: 85.8, numerator: 2.6, denominator: 3, status: "ok", trend: [] },
          { name: "DIP-1 · C型号", entity: { line: "DIP-1", model: "C型号" }, value: 90.6, numerator: 1.8, denominator: 2, status: "ok", trend: [] },
        ] },
      production_achieve: { label: "产出达成率", target: 95.0, higher_is_better: true, dimension: "产线 · 型号",
        definition: "实际产出数 ÷ 计划产出数。",
        ask: "最近7天各产线产出达成率如何？最低的是哪条线？",
        rows: [
          { name: "SMT-2 · B型号", entity: { line: "SMT-2", model: "B型号" }, value: 92.3, numerator: 2770, denominator: 3000, status: "warn", trend: [] },
          { name: "SMT-1 · A型号", entity: { line: "SMT-1", model: "A型号" }, value: 97.3, numerator: 5840, denominator: 6000, status: "ok", trend: [] },
          { name: "DIP-1 · C型号", entity: { line: "DIP-1", model: "C型号" }, value: 100.0, numerator: 1800, denominator: 1800, status: "ok", trend: [] },
        ] },
    },
    quality: {
      defect_rate: { label: "不良率", target: 2.0, higher_is_better: false, dimension: "产线 · 型号",
        definition: "不良数 ÷ 受检总数。",
        ask: "最近7天各产线不良率如何？最高的是哪条线？" },
      fpy: { label: "直通率 FPY", target: 98.0, higher_is_better: true, dimension: "产线 · 型号",
        definition: "一次通过良品数 ÷ 投入数（≈ 1 − 不良率）。",
        ask: "最近7天各产线直通率如何？最低的是哪条线？" },
      incoming_pass_rate: { label: "来料批次合格率", target: 90.0, higher_is_better: true, dimension: "供应商",
        definition: "IQC 判定合格（pass）批次数 ÷ 窗口内来料批次总数。",
        ask: "最近7天各供应商来料批次合格率如何？最低的是哪家？",
        rows: [
          { name: "国巨", entity: { supplier: "国巨" }, value: 0.0, numerator: 0, denominator: 1, status: "danger", trend: [] },
          { name: "天马微电子", entity: { supplier: "天马微电子" }, value: 0.0, numerator: 0, denominator: 1, status: "danger", trend: [] },
          { name: "三星电机", entity: { supplier: "三星电机" }, value: 50.0, numerator: 1, denominator: 2, status: "danger", trend: [] },
          { name: "千住金属", entity: { supplier: "千住金属" }, value: 50.0, numerator: 1, denominator: 2, status: "danger", trend: [] },
        ] },
      test_pass_rate: { label: "测试通过率", target: 95.0, higher_is_better: true, dimension: "测试类型",
        definition: "测试通过数 ÷ 测试总数（按测试类型分解）。",
        ask: "最近7天各测试类型通过率如何？最低的是哪类？",
        rows: [
          { name: "软件", entity: { test_type: "软件" }, value: 80.0, numerator: 4, denominator: 5, status: "danger", trend: [] },
          { name: "可靠性", entity: { test_type: "可靠性" }, value: 87.5, numerator: 7, denominator: 8, status: "warn", trend: [] },
          { name: "功能", entity: { test_type: "功能" }, value: 100.0, numerator: 9, denominator: 9, status: "ok", trend: [] },
        ] },
      std_conformity: { label: "标准三率符合率", target: 88.2, higher_is_better: true, dimension: "型号 · 工序",
        definition: "作业标准符合项数 ÷ 审核项总数（符合率 = 完整率 × 完善率 × 遵守率）。",
        ask: "最近7天各型号工序标准符合率如何？最低的是哪一项？",
        rows: [
          { name: "B型号 · 测试", entity: { model: "B型号", pstep: "测试" }, value: 70.0, numerator: 14, denominator: 20, status: "danger", trend: [] },
          { name: "B型号 · 波峰焊", entity: { model: "B型号", pstep: "波峰焊" }, value: 75.0, numerator: 15, denominator: 20, status: "danger", trend: [] },
          { name: "B型号 · 贴片", entity: { model: "B型号", pstep: "贴片" }, value: 75.0, numerator: 15, denominator: 20, status: "danger", trend: [] },
          { name: "C型号 · 包装", entity: { model: "C型号", pstep: "包装" }, value: 80.0, numerator: 16, denominator: 20, status: "warn", trend: [] },
          { name: "C型号 · 插件", entity: { model: "C型号", pstep: "插件" }, value: 80.0, numerator: 16, denominator: 20, status: "warn", trend: [] },
        ] },
      complaint_close_rate: { label: "客诉闭环率", target: 80.0, higher_is_better: true, dimension: "客户 · 型号",
        definition: "已关闭（closed）客诉单数 ÷ 客诉单总数（快照口径，不做时间窗口过滤）。",
        ask: "各客户型号的客诉闭环率如何？哪些还没闭环？",
        rows: [
          { name: "华为终端 · A型号", entity: { customer: "华为终端", model: "A型号" }, value: 0.0, numerator: 0, denominator: 1, status: "danger", trend: [] },
          { name: "OPPO · A型号", entity: { customer: "OPPO", model: "A型号" }, value: 0.0, numerator: 0, denominator: 1, status: "danger", trend: [] },
          { name: "美的 · B型号", entity: { customer: "美的", model: "B型号" }, value: 0.0, numerator: 0, denominator: 1, status: "danger", trend: [] },
          { name: "大疆 · C型号", entity: { customer: "大疆", model: "C型号" }, value: 100.0, numerator: 1, denominator: 1, status: "ok", trend: [] },
          { name: "小米 · B型号", entity: { customer: "小米", model: "B型号" }, value: 100.0, numerator: 1, denominator: 1, status: "ok", trend: [] },
        ] },
    },
    orders: {
      otd: { label: "OTD 准时交付率", target: 98.0, higher_is_better: true, dimension: "客户 · 等级",
        definition: "准时交付（实际发货日 ≤ 客户要求交付日）订单数 ÷ 订单总数。",
        ask: "最近7天各客户 OTD 准时交付率如何？最低的是哪个客户？" },
      otif: { label: "OTIF 准时足额率", target: 95.0, higher_is_better: true, dimension: "客户 · 等级",
        definition: "同时满足准时且足额的订单数 ÷ 订单总数。",
        ask: "最近7天各客户 OTIF 准时足额率如何？最低的是哪个客户？" },
      delay_rate: { label: "延期订单率", target: 5.0, higher_is_better: false, dimension: "客户 · 等级",
        definition: "延期天数 > 0 的订单数 ÷ 订单总数（越低越好）。",
        ask: "最近7天各客户延期订单率如何？最高的是哪个客户？延期原因有哪些？" },
      kit_rate: { label: "齐套率", target: 95.0, higher_is_better: true, dimension: "型号",
        definition: "已齐套料数 ÷ 应齐套料数。",
        ask: "最近7天各型号订单齐套率如何？最低的是哪个型号？缺料集中在哪些料号？" },
      material_shortage_rate: { label: "缺料项占比", target: 10.0, higher_is_better: false, dimension: "仓库",
        definition: "在库量低于安全库存的物料行数 ÷ 物料行总数（快照口径）。",
        ask: "当前各仓库缺料项占比如何？缺料集中在哪些料号？",
        rows: [
          { name: "电子料仓", entity: { warehouse: "电子料仓" }, value: 70.0, numerator: 7, denominator: 10, status: "danger", trend: [] },
          { name: "包材仓", entity: { warehouse: "包材仓" }, value: 50.0, numerator: 1, denominator: 2, status: "danger", trend: [] },
          { name: "结构件仓", entity: { warehouse: "结构件仓" }, value: 20.0, numerator: 1, denominator: 5, status: "danger", trend: [] },
          { name: "标准件仓", entity: { warehouse: "标准件仓" }, value: 0.0, numerator: 0, denominator: 1, status: "ok", trend: [] },
        ] },
      milestone_done_rate: { label: "里程碑达成率", target: 80.0, higher_is_better: true, dimension: "型号",
        definition: "已完成（done）里程碑数 ÷ 里程碑总数（快照口径）。",
        ask: "各型号项目里程碑达成率如何？哪些里程碑临期或延期？",
        rows: [
          { name: "C型号", entity: { model: "C型号" }, value: 0.0, numerator: 0, denominator: 2, status: "danger", trend: [] },
          { name: "B型号", entity: { model: "B型号" }, value: 33.3, numerator: 1, denominator: 3, status: "danger", trend: [] },
          { name: "A型号", entity: { model: "A型号" }, value: 100.0, numerator: 1, denominator: 1, status: "ok", trend: [] },
        ] },
      task_backlog_rate: { label: "待人工确认占比", target: 50.0, higher_is_better: false, dimension: "流程",
        definition: "待人工确认（waiting_hitl）工单数 ÷ 工单总数（快照口径）。",
        ask: "当前各流程待人工确认工单占比如何？哪些 SLA 临近？",
        rows: [
          { name: "hitl_complaint_reply", entity: { wf: "hitl_complaint_reply" }, value: 100.0, numerator: 48, denominator: 48, status: "danger", trend: [] },
          { name: "hitl_disposition", entity: { wf: "hitl_disposition" }, value: 100.0, numerator: 45, denominator: 45, status: "danger", trend: [] },
          { name: "hitl_eta", entity: { wf: "hitl_eta" }, value: 100.0, numerator: 50, denominator: 50, status: "danger", trend: [] },
          { name: "hitl_restart", entity: { wf: "hitl_restart" }, value: 100.0, numerator: 50, denominator: 50, status: "danger", trend: [] },
          { name: "hitl_delivery_commit", entity: { wf: "hitl_delivery_commit" }, value: 100.0, numerator: 7, denominator: 7, status: "danger", trend: [] },
        ] },
    },
    // ---- SPC 过程能力（screen=spc）----
    // 结构对齐 manufacturing / quality：L2 为按维度分解；每行自带 columns + records，
    // 供 mfgDrillFallback 定位实体后拼出 L3「关联原始记录」。
    spc: {
      spc_capability: { label: "子组超差率", target: 5.0, higher_is_better: false, unit: "%", dimension: "特性",
        definition: "越限测量值条数 ÷ 测量条数（子组口径的过程越限比例，与 Cpk 同源：Cpk 高则超差率低）。",
        ask: "各特性的过程超差率如何？哪个特性最需要加严？",
        rows: [
          { name: "回流焊峰值温度(℃)", entity: { characteristic: "回流焊峰值温度(℃)" }, line: "", model: "", wo: "",
            value: 8.0, status: "danger", numerator: 8, denominator: 100, rank: 1, trend: [],
            columns: _SPC_MEAS_COLS, records: [
              { measured_at: "2026-10-14 08:12:00", characteristic: "回流焊峰值温度(℃)", subgroup_no: 20, seq: 3, value: 252.6, usl: 250, lsl: 235, source: "AOI 在线" },
              { measured_at: "2026-10-13 20:41:00", characteristic: "回流焊峰值温度(℃)", subgroup_no: 19, seq: 5, value: 251.1, usl: 250, lsl: 235, source: "AOI 在线" },
              { measured_at: "2026-10-12 14:05:00", characteristic: "回流焊峰值温度(℃)", subgroup_no: 16, seq: 2, value: 251.8, usl: 250, lsl: 235, source: "回流炉温记录" },
            ] },
          { name: "锡膏厚度(μm)", entity: { characteristic: "锡膏厚度(μm)" }, line: "", model: "", wo: "",
            value: 3.0, status: "ok", numerator: 3, denominator: 100, rank: 2, trend: [],
            columns: _SPC_MEAS_COLS, records: [
              { measured_at: "2026-10-14 09:03:00", characteristic: "锡膏厚度(μm)", subgroup_no: 20, seq: 1, value: 161.4, usl: 160, lsl: 120, source: "SPI 在线" },
              { measured_at: "2026-10-13 10:22:00", characteristic: "锡膏厚度(μm)", subgroup_no: 17, seq: 4, value: 159.7, usl: 160, lsl: 120, source: "SPI 在线" },
              { measured_at: "2026-10-11 16:48:00", characteristic: "锡膏厚度(μm)", subgroup_no: 12, seq: 5, value: 160.9, usl: 160, lsl: 120, source: "SPI 离线抽检" },
            ] },
          { name: "焊点高度(mm)", entity: { characteristic: "焊点高度(mm)" }, line: "", model: "", wo: "",
            value: 0.0, status: "ok", numerator: 0, denominator: 100, rank: 3, trend: [],
            columns: _SPC_MEAS_COLS, records: [
              { measured_at: "2026-10-14 09:35:00", characteristic: "焊点高度(mm)", subgroup_no: 20, seq: 2, value: 0.752, usl: 0.9, lsl: 0.6, source: "AOI 在线" },
              { measured_at: "2026-10-12 11:19:00", characteristic: "焊点高度(mm)", subgroup_no: 15, seq: 1, value: 0.741, usl: 0.9, lsl: 0.6, source: "AOI 在线" },
              { measured_at: "2026-10-09 15:52:00", characteristic: "焊点高度(mm)", subgroup_no: 7, seq: 4, value: 0.766, usl: 0.9, lsl: 0.6, source: "人工抽检" },
            ] },
        ] },
      spc_pareto: { label: "缺陷类型不良率", target: 10.0, higher_is_better: false, unit: "%", dimension: "不良类型",
        definition: "各缺陷类型不良数 ÷ 受检总数（柏拉图关键少数取证）。",
        ask: "各缺陷类型不良率如何？最集中的不良模式是哪类？",
        rows: [
          { name: "元件立碑", entity: { defect_type: "元件立碑" }, line: "", model: "", wo: "",
            value: 18.0, status: "danger", numerator: 36, denominator: 200, rank: 1, trend: [],
            columns: _SPC_DEFECT_COLS, records: [
              { inspect_time: "2026-10-14 10:21:00", batch_no: "BATCH-A-1008", product_model: "A型号", line_code: "SMT-1", defect_type: "元件立碑", defect_qty: 6, total_qty: 200 },
              { inspect_time: "2026-10-12 08:47:00", batch_no: "BATCH-A-1002", product_model: "B型号", line_code: "SMT-2", defect_type: "元件立碑", defect_qty: 4, total_qty: 200 },
            ] },
          { name: "连锡", entity: { defect_type: "连锡" }, line: "", model: "", wo: "",
            value: 13.0, status: "warn", numerator: 26, denominator: 200, rank: 2, trend: [],
            columns: _SPC_DEFECT_COLS, records: [
              { inspect_time: "2026-10-13 19:12:00", batch_no: "BATCH-C-2005", product_model: "C型号", line_code: "DIP-1", defect_type: "连锡", defect_qty: 5, total_qty: 200 },
              { inspect_time: "2026-10-11 13:58:00", batch_no: "BATCH-B-2001", product_model: "B型号", line_code: "DIP-1", defect_type: "连锡", defect_qty: 3, total_qty: 200 },
            ] },
          { name: "少锡/虚焊", entity: { defect_type: "少锡/虚焊" }, line: "", model: "", wo: "",
            value: 6.0, status: "ok", numerator: 12, denominator: 200, rank: 3, trend: [],
            columns: _SPC_DEFECT_COLS, records: [
              { inspect_time: "2026-10-14 09:44:00", batch_no: "BATCH-A-1008", product_model: "A型号", line_code: "SMT-1", defect_type: "少锡/虚焊", defect_qty: 3, total_qty: 200 },
              { inspect_time: "2026-10-10 17:30:00", batch_no: "BATCH-A-1001", product_model: "A型号", line_code: "SMT-1", defect_type: "少锡/虚焊", defect_qty: 2, total_qty: 200 },
            ] },
          { name: "锡珠", entity: { defect_type: "锡珠" }, line: "", model: "", wo: "",
            value: 4.5, status: "ok", numerator: 9, denominator: 200, rank: 4, trend: [],
            columns: _SPC_DEFECT_COLS, records: [
              { inspect_time: "2026-10-13 11:06:00", batch_no: "BATCH-B-2001", product_model: "B型号", line_code: "SMT-2", defect_type: "锡珠", defect_qty: 2, total_qty: 200 },
              { inspect_time: "2026-10-12 20:25:00", batch_no: "BATCH-B-1004", product_model: "B型号", line_code: "SMT-2", defect_type: "锡珠", defect_qty: 2, total_qty: 200 },
            ] },
          { name: "元件偏移", entity: { defect_type: "元件偏移" }, line: "", model: "", wo: "",
            value: 3.0, status: "ok", numerator: 6, denominator: 200, rank: 5, trend: [],
            columns: _SPC_DEFECT_COLS, records: [
              { inspect_time: "2026-10-14 15:09:00", batch_no: "BATCH-C-2005", product_model: "C型号", line_code: "SMT-2", defect_type: "元件偏移", defect_qty: 2, total_qty: 200 },
              { inspect_time: "2026-10-09 10:38:00", batch_no: "BATCH-C-1003", product_model: "C型号", line_code: "SMT-2", defect_type: "元件偏移", defect_qty: 1, total_qty: 200 },
            ] },
        ] },
    },
  };

  /* ---------------- 三级下钻（工段 / 工序 / 工位）离线模板 ----------------
     指标 → 工段/工序/工位清单 + 缺陷模式；行级分子/分母按权重分摊（末位取余数，
     求和严格回原值），口径与真实链路一致，仅供后端离线时演示。 */
  const DRILL_STAGE_TPL = {
    availability: { label: "停机原因", modes: ["抛料停机", "换料调机", "设备故障", "计划保养"], steps: [
      ["SMT段", "锡膏印刷", [["SPI-01", "印刷机#1", "EQ-SPI-01"], ["SPI-02", "印刷机#2", "EQ-SPI-02"]]],
      ["SMT段", "贴片", [["SMT-M1", "贴片机#1", "EQ-SMT-M1"], ["SMT-M2", "贴片机#2", "EQ-SMT-M2"]]],
      ["SMT段", "回流焊", [["RF-01", "回流炉#1", "EQ-RF-01"]]],
      ["DIP段", "插件", [["DIP-I1", "插件线#1", "EQ-DIP-I1"]]],
      ["DIP段", "波峰焊", [["WS-01", "波峰炉#1", "EQ-WS-01"]]]] },
    scrap_rate: { label: "报废原因", modes: ["连锡报废", "元件损伤", "外观不良"], steps: [
      ["SMT段", "锡膏印刷", [["SPI-01", "印刷机#1", "EQ-SPI-01"]]],
      ["SMT段", "贴片", [["SMT-M1", "贴片机#1", "EQ-SMT-M1"]]],
      ["DIP段", "波峰焊", [["WS-01", "波峰炉#1", "EQ-WS-01"]]]] },
    test_fpy: { label: "测试失效项", modes: ["ICT 开路", "功能失效", "老化失效"], steps: [
      ["测试段", "ICT", [["ICT-01", "ICT 治具#1", "EQ-ICT-01"]]],
      ["测试段", "功能测试", [["FCT-01", "功能测试台#1", "EQ-FCT-01"], ["FCT-02", "功能测试台#2", "EQ-FCT-02"]]],
      ["测试段", "老化", [["AGE-01", "老化房A", "EQ-AGE-01"]]]] },
    defect_rate: { label: "不良模式", modes: ["元件立碑", "锡膏偏移", "连锡", "外观划伤"], steps: [
      ["SMT段", "锡膏印刷", [["SPI-01", "印刷机#1", "EQ-SPI-01"], ["SPI-02", "印刷机#2", "EQ-SPI-02"]]],
      ["SMT段", "贴片", [["SMT-M1", "贴片机#1", "EQ-SMT-M1"], ["SMT-M2", "贴片机#2", "EQ-SMT-M2"]]],
      ["SMT段", "回流焊", [["RF-01", "回流炉#1", "EQ-RF-01"]]]] },
    fpy: { label: "不良模式", modes: ["元件立碑", "锡膏偏移", "连锡"], steps: [
      ["SMT段", "贴片", [["SMT-M1", "贴片机#1", "EQ-SMT-M1"], ["SMT-M2", "贴片机#2", "EQ-SMT-M2"]]],
      ["SMT段", "回流焊", [["RF-01", "回流炉#1", "EQ-RF-01"]]]] },
    // 报工完成率口径取自 work_report，无工位层 → 只到工序
    report_rate: { label: "缺陷模式", modes: [], steps: [
      ["SMT段", "贴片", []], ["DIP段", "插件", []], ["DIP段", "波峰焊", []],
      ["测试段", "测试", []], ["组装段", "包装", []]] },
  };
  const _SPLIT_W = [3, 2, 2, 1, 1, 1];
  const _SPLIT_SH = ["白班", "夜班"];
  const _SPLIT_OP = ["张建国", "周慧", "刘敏", "王振"];

  function demoStatus(v, target, hi) {
    if (hi) return v >= target ? "ok" : (v >= target * 0.9 ? "warn" : "danger");
    return v <= target ? "ok" : (v <= target * 1.5 ? "warn" : "danger");
  }

  /** 行级 → 工段/工序/工位/班次 拆分（末位取余数，求和严格回原值）。 */
  function splitDemoBreakdown(row, tpl, target, hi) {
    const steps = tpl.steps || [];
    const modes = tpl.modes || [];
    const num = row.numerator || 0, den = row.denominator || 0;
    const tot = steps.reduce((a, _s, i) => a + _SPLIT_W[i % _SPLIT_W.length], 0) || 1;
    let accN = 0, accD = 0;
    const parts = [];
    steps.forEach((s, i) => {
      const last = i === steps.length - 1;
      const w = _SPLIT_W[i % _SPLIT_W.length];
      const n = last ? num - accN : Math.round(num * w / tot);
      const d = last ? den - accD : Math.round(den * w / tot);
      accN += n; accD += d;
      const v = d ? Math.round(1000 * n / d) / 10 : 0;
      parts.push({ stage: s[0], step: s[1], stations: s[2] || [], num: n, den: d, value: v,
                   loss: (hi ? Math.max(d - n, 0) : Math.max(n, 0)) });
    });
    const totalLoss = parts.reduce((a, p) => a + p.loss, 0) || 1;
    const stages = [], stations = [];
    parts.forEach((p) => {
      const share = Math.round(1000 * p.loss / totalLoss) / 10;
      let st = stages.find((x) => x.stage === p.stage);
      if (!st) { st = { stage: p.stage, num: 0, den: 0, value: 0, loss: 0, steps: [] };
                 stages.push(st); }
      st.steps.push({ step: p.step, stage: p.stage, num: p.num, den: p.den, value: p.value,
                      loss: p.loss, share: share, status: demoStatus(p.value, target, hi) });
      st.num += p.num; st.den += p.den; st.loss += p.loss;
      // 工位：先按工位分摊，再按班次二次分摊（两级都取余数保证求和回原值）
      const sn = p.stations.length;
      const sw = (sn + 1) * sn / 2 || 1;
      let saN = 0, saD = 0;
      p.stations.forEach((s, j) => {
        const lastS = j === sn - 1;
        const sN = lastS ? p.num - saN : Math.round(p.num * (sn - j) / sw);
        const sD = lastS ? p.den - saD : Math.round(p.den * (sn - j) / sw);
        saN += sN; saD += sD;
        let shN = 0, shD = 0;
        _SPLIT_SH.forEach((sh, k) => {
          const lastSh = k === _SPLIT_SH.length - 1;
          const n2 = lastSh ? sN - shN : Math.round(sN * 0.55);
          const d2 = lastSh ? sD - shD : Math.round(sD * 0.55);
          shN += n2; shD += d2;
          const v2 = d2 ? Math.round(1000 * n2 / d2) / 10 : 0;
          stations.push({ stage: p.stage, step: p.step, station_no: s[0], station_name: s[1],
                          equipment_no: s[2], shift: sh, operator: _SPLIT_OP[(j + k) % _SPLIT_OP.length],
                          cycle_time_s: 32, defect_mode: modes[(j + k) % Math.max(1, modes.length)] || "—",
                          num: n2, den: d2, value: v2, status: demoStatus(v2, target, hi) });
        });
      });
    });
    stages.forEach((st) => { st.value = st.den ? Math.round(1000 * st.num / st.den) / 10 : 0; });
    const mw = (modes.length + 1) * modes.length / 2 || 1;
    let mA = 0;
    const pareto = modes.map((m, i) => {
      const last = i === modes.length - 1;
      const v = last ? totalLoss - mA : Math.round(totalLoss * (modes.length - i) / mw);
      mA += v;
      return { name: m, value: v, share: Math.round(1000 * v / totalLoss) / 10 };
    }).filter((x) => x.value > 0);
    return { stages: stages, stations: stations, pareto: pareto,
             pareto_label: tpl.label || "缺陷模式" };
  }

  DASHBOARD.drill = {};
  Object.keys(DRILL_META).forEach((screen) => {
    const src = DASHBOARD[screen];
    DASHBOARD.drill[screen] = {};
    Object.keys(DRILL_META[screen]).forEach((kpi) => {
      const meta = DRILL_META[screen][kpi];
      const tpl = DRILL_STAGE_TPL[kpi];
      const ranking = (src.rankings || []).find((r) => r.key === kpi);
      DASHBOARD.drill[screen][kpi] = Object.assign({
        level: 2, screen: screen, kpi: kpi, unit: "%",
        source: "demo", generated_at: src.generated_at, window: src.window,
        degraded: true,
        degraded_reason: "后端离线 · 演示数据（离线仅含 TOP5 分组，逐日/原始记录需后端在线）",
        rows: ((ranking && ranking.items) || []).map((it) => {
          const row = Object.assign({}, it, { trend: [] });
          return tpl ? Object.assign(row, splitDemoBreakdown(row, tpl, meta.target,
            meta.higher_is_better)) : row;
        }),
      }, meta);
    });
  });

  /* 企业综合助手 · 制造数据速查（后端离线时的兜底演示数据）
     结构对齐 GET /api/v2/office/snapshot 的 data.blocks（字段名一致，便于同构渲染）。 */
  const OFFICE_SNAPSHOT = {
    source: "演示数据（后端离线兜底）",
    degraded: true,
    blocks: {
      material_stock: {
        authorized: true, ok: true, count: 6,
        note: "材料库存（按料号×仓库；可用量=在库-预留，冻结量不计可用）",
        rows: [
          { part_no: "CAP-0402-10uF", part_name: "贴片电容10uF", category: "电子料", warehouse: "电子料仓", bin_location: "A-01-03", on_hand_qty: 150000, reserved_qty: 40000, available_qty: 110000, safety_stock: 50000, age_days: 12 },
          { part_no: "RES-0603-1k", part_name: "贴片电阻1kΩ", category: "电子料", warehouse: "电子料仓", bin_location: "A-01-07", on_hand_qty: 40000, reserved_qty: 5000, available_qty: 35000, safety_stock: 60000, age_days: 34 },
          { part_no: "IC-MCU-32bit", part_name: "主控MCU", category: "电子料", warehouse: "电子料仓", bin_location: "A-02-01", on_hand_qty: 5000, reserved_qty: 4500, available_qty: 500, safety_stock: 8000, age_days: 20 },
          { part_no: "CAP-0805-22uF", part_name: "贴片电容22uF", category: "电子料", warehouse: "电子料仓", bin_location: "A-01-05", on_hand_qty: 25000, reserved_qty: 0, available_qty: 25000, safety_stock: 15000, age_days: 9 },
          { part_no: "CON-TYPEC-16P", part_name: "TYPE-C连接器", category: "结构件", warehouse: "结构件仓", bin_location: "B-01-02", on_hand_qty: 60000, reserved_qty: 20000, available_qty: 40000, safety_stock: 30000, age_days: 8 },
          { part_no: "PACK-BOX-A", part_name: "包装彩盒A", category: "包材", warehouse: "包材仓", bin_location: "E-01-03", on_hand_qty: 4500, reserved_qty: 0, available_qty: 4500, safety_stock: 5000, age_days: 210 },
        ],
      },
      shortage: {
        authorized: true, ok: true, count: 3,
        note: "缺料清单（可用量低于安全库存，含缺口）",
        rows: [
          { part_no: "RES-0603-1k", part_name: "贴片电阻1kΩ", category: "电子料", available_qty: 35000, safety_stock: 60000, shortage_qty: 25000 },
          { part_no: "IC-MCU-32bit", part_name: "主控MCU", category: "电子料", available_qty: 2000, safety_stock: 8000, shortage_qty: 6000 },
          { part_no: "LCD-5.5-IPS", part_name: "5.5寸液晶屏", category: "电子料", available_qty: 700, safety_stock: 3000, shortage_qty: 2300 },
        ],
      },
      finished_stock: {
        authorized: true, ok: true, count: 5,
        note: "成品库存（型号×仓库；可发量 ATP=在库-预留）",
        rows: [
          { product_model: "A型号", warehouse: "深圳成品仓", batch_no: "BATCH-A-1015", on_hand_qty: 1200, reserved_qty: 800, available_qty: 400, safety_stock: 1000, age_days: 2 },
          { product_model: "B型号", warehouse: "深圳成品仓", batch_no: "BATCH-B-1014", on_hand_qty: 5000, reserved_qty: 500, available_qty: 4500, safety_stock: 2000, age_days: 3 },
          { product_model: "C型号", warehouse: "华东成品仓", batch_no: "BATCH-C-1009", on_hand_qty: 1500, reserved_qty: 600, available_qty: 900, safety_stock: 800, age_days: 6 },
          { product_model: "D型号", warehouse: "深圳成品仓", batch_no: "BATCH-D-1001", on_hand_qty: 3200, reserved_qty: 1200, available_qty: 2000, safety_stock: 1500, age_days: 5 },
          { product_model: "E型号", warehouse: "保税仓", batch_no: "BATCH-E-1001", on_hand_qty: 700, reserved_qty: 650, available_qty: 50, safety_stock: 600, age_days: 7 },
        ],
      },
      incoming_batch: {
        authorized: true, ok: true, count: 5,
        note: "近期来料批次（IQC 结论：合格/不合格/待检/让步接收）",
        rows: [
          { batch_no: "MAT-SCR-1018", part_no: "SCREW-M8-20", part_name: "内六角螺栓M8×20", supplier: "东莞紧固", arrival_time: "2026-10-14 15:00:00", qty: 100000, qualified_qty: 100000, inspect_status: "pass", warehouse: "标准件仓", bin_location: "D-01-01" },
          { batch_no: "MAT-CON-1014", part_no: "CON-TYPEC-16P", part_name: "TYPE-C连接器", supplier: "立讯精密", arrival_time: "2026-10-14 09:30:00", qty: 80000, qualified_qty: 80000, inspect_status: "pass", warehouse: "结构件仓", bin_location: "B-01-02" },
          { batch_no: "MAT-SOL-1015", part_no: "SOLDER-PASTE-SAC305", part_name: "无铅锡膏SAC305", supplier: "千住金属", arrival_time: "2026-10-13 08:00:00", qty: 200, qualified_qty: 200, inspect_status: "pass", warehouse: "辅料仓", bin_location: "C-01-01" },
          { batch_no: "MAT-LCD-1024", part_no: "LCD-5.5-IPS", part_name: "5.5寸液晶屏", supplier: "天马微电子", arrival_time: "2026-10-12 13:15:00", qty: 1200, qualified_qty: 1200, inspect_status: "pending", warehouse: "电子料仓", bin_location: "A-03-04" },
          { batch_no: "MAT-CAP-1013", part_no: "CAP-0805-22uF", part_name: "贴片电容22uF", supplier: "三星电机", arrival_time: "2026-10-09 08:45:00", qty: 30000, qualified_qty: 0, inspect_status: "fail", warehouse: "电子料仓", bin_location: "A-01-05" },
        ],
      },
    },
  };

  window.DEMO = {
    ROLES, ROLE_GROUPS, ALERT_SUMMARY, ROLE_KPIS, DEMO_TODOS, ORDERS, ORDERS_DASHBOARD, SPC_DASHBOARD, ALERTS, REPORTS, DEMO_人工确认, CHAT_DEMO, CHAT_DEMOS, DASHBOARD, OFFICE_SNAPSHOT,
  };
})();
