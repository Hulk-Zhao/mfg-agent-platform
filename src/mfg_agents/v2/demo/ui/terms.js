/**
 * 术语映射层（terms.js）——把后端内部标识翻译成一线业务语言。
 *
 * 设计约束：
 *  - 纯前端映射表，零后端改动（后端只回 `scenario_key`，无场景列表端点）；
 *  - 单一事实源：场景中文名对齐 `src/mfg_agents/v2/scenarios/*.yaml` 的 `name`
 *    字段，改动场景名时需同步本文件（两处都要改）；
 *  - 加载顺序：demo-data.js → terms.js → app.js → screens.js（由 index.html 保证）；
 *  - 任何未登记的 key 走兜底：原样返回，绝不显示空白。
 *
 * 导出：window.TERMS
 */
(function () {
  "use strict";

  /* ============================================================
   * 一、场景 key → 中文场景名（对齐 v2/scenarios/*.yaml 的 name）
   * ============================================================ */

  const SCENARIO_NAMES = {
    line_stop_v2: "停线应急协同",
    customer_complaint_v2: "客户投诉闭环",
    customer_complain_v2: "客户投诉闭环", // 兼容后端历史拼写
    delivery_status_v2: "出货风险协同",
    equipment_warning_v2: "设备预警与维护协同",
    order_change_impact_v2: "订单变更影响评估",
    incoming_quality_v2: "来料异常联动",
  };

  /* ============================================================
   * 二、路由等级 / 匹配度 / 责任域 / 通道
   * ============================================================ */

  const LEVEL_LABELS = {
    L0: "精准匹配",
    L1: "语义匹配",
    L2: "转人工兜底",
  };

  const DOMAIN_LABELS = {
    manufacturing: "制造",
    quality: "品质",
    supply_chain: "供应链",
    equipment: "设备",
    business: "业务",
    sales: "业务",
    finance: "财务",
    planning: "计划",
    material_control: "物控",
    procurement: "采购",
    warehouse: "仓储",
    rd_engineering: "研发工程",
    product_engineering: "产品工程",
    orchestration: "意图识别",
    report: "报告",
    hitl: "人工确认",
    workflow: "工单",
    router: "路由",
  };

  const CHANNEL_LABELS = {
    sql: "生产与质量系统数据",
    rag: "历史案例库",
    workflow: "工单系统",
    hitl: "人工确认记录",
    compute: "平台计算",
    template: "规则模板",
    report: "报告生成",
    data_gap: "暂无数据源",
    node_timing: "处理耗时",
  };

  /** 数据表 → 业务系统口径（A2：不展示表名，改说"数据来源"） */
  const TABLE_LABELS = {
    production_output: "MES 报工",
    line_stop_event: "MES 停线记录",
    quality_defect: "QMS 检验记录",
    material_batch: "SCM 来料检验",
    equipment_log: "EAM 设备台账",
    inventory: "WMS 库存",
    shipment: "TMS 发运",
    customer_complaint: "CRM 客诉单",
    finance_policy: "财务制度库",
    admin_policy: "行政制度库",
    kbqa_policy: "企业制度库",
    project_milestone: "NPI 项目库",
    test_records: "可靠性测试库",
  };

  /** 注册查询名 → 业务名（对齐 v2/data/queries.yaml 的 desc） */
  const QUERY_LABELS = {
    mfg_oee: "当日设备效率与产出",
    mfg_stop_ongoing: "进行中的停线事件",
    quality_defect_dist: "不良类型分布",
    supply_batches_today: "今日到货批次",
    equip_alarms: "设备异常告警",
    warehouse_available: "型号可用库存",
    shipment_status: "发货单状态与预计到货",
    iqc_batches_recent: "本周期不合格来料批次",
    batch_supplier_by_no: "按批号查供应商与检验结论",
    supplier_followup: "供应商到货与不合格统计",
    inventory_available_all: "全部型号可用库存",
    complaint_detail: "客诉单明细",
    quality_defect_by_batch: "按批号的不良分布",
    orders_by_model: "型号订单与交期",
    shortage_by_model: "型号库存与订单缺口",
    plan_by_model: "型号排产与产出",
    output_by_model: "型号产出达成与平均效率",
    plan_impact_by_material: "来料异常影响的排产",
    finance_policy_search: "财务制度查询",
    admin_policy_search: "行政制度查询",
    kbqa_policy_search: "企业制度查询",
    project_milestones: "NPI 项目里程碑",
    test_records_recent: "最近可靠性测试记录",
    asset_status_by_equipment: "设备状态与参数趋势",
    quality_trace_by_batch: "按批次做正反向追溯",
    atp_by_model: "型号可承诺库存",
    supply_risk_by_part: "按料号查来料风险",
    daily_output_by_model: "近 14 天每日产出",
  };

  /* ============================================================
   * 三、内部简报 key → 业务表述
   * ============================================================ */

  const BRIEF_LABELS = {
    // --- 停线应急 ---
    停线确认: "是否已停线",
    生产: "生产影响",
    品质: "品质影响",
    供应链: "供应链影响",
    设备: "设备影响",
    仓储: "库存影响",
    FA评级: "风险等级",
    风险评级: "风险等级",
    // --- 通用 ---
    数据缺口: "数据缺口",
    // --- 客诉闭环 ---
    客诉摘要: "问题概述",
    "8D报告": "整改计划（D1-D8）",
    过程分析: "制程分析结论",
    供应商侧: "责任方判断",
    设计根因: "设计侧根因",
    对客口径: "客户沟通建议",
    // --- 出货风险 ---
    ATP承诺分析: "可承诺交付分析",
    风险订单: "受影响订单",
    订单: "订单影响",
    排产: "排产影响",
    库存评估: "库存结论",
    产出: "产出影响",
    物流在途: "在途物流",
    // --- 设备预警 ---
    设备告警: "设备异常",
    维保建议: "维保建议",
    换产窗口: "换产窗口",
    参数建议: "参数调整建议",
    备件需求: "备件需求",
    // --- 来料异常 ---
    来料不合格判定: "来料判定结论",
    缺陷等级: "缺陷等级",
    处置建议: "处置建议",
    采购跟催: "采购跟催",
    排产影响: "排产影响",
    物料评估: "物料评估",
    // --- 订单变更 ---
    物料齐套: "物料齐套情况",
    制造产能: "制造产能",
  };

  /* ============================================================
   * 四、异常三级（A5）
   * ============================================================ */

  const LEVEL_LABELS_SEVERITY = { normal: "正常", warn: "关注", alert: "紧急" };

  function _ratingOf(result) {
    const brief = (result && result.internal_brief) || {};
    const text = String(brief["FA评级"] || brief["风险评级"] || "");
    const m = /评级\s*([A-D])/.exec(text) || /([A-D])\s*级/.exec(text);
    return m ? m[1] : "";
  }

  /**
   * 结果 → 三级严重度。判据全部来自后端既有字段，可审计、不臆造：
   *   alert：A/D 级风险 / degraded（受限输出）/ 存在数据缺口 / 人工兜底
   *   warn ：B 级风险 / 低置信路由
   *   normal：其余
   */
  function severity(result) {
    if (!result) return "normal";
    const prov = result.provenance || [];
    const hasGap = prov.some((p) => p && p.channel === "data_gap");
    if (result.fallback === "manual_ticket" || result.degraded || hasGap) return "alert";
    const rating = _ratingOf(result);
    if (rating === "A" || rating === "D") return "alert";
    if (rating === "B") return "warn";
    const lowConf = (result.route && result.route.low_confidence) || result.low_confidence;
    if (lowConf) return "warn";
    return "normal";
  }

  /** 一行结论（A1 #2）：动词开头。停线类由"是否停线 + 生产结论 + 恢复时间"合成；
   *  其余优先取报告首句（去称呼），最后兜底拼简报要点。 */
  function headline(result) {
    const brief = (result && result.internal_brief) || {};
    const eta = etaText(result);
    const stopKey = brief["停线确认"] !== undefined ? "停线确认"
      : brief["是否已停线"] !== undefined ? "是否已停线" : "";
    if (stopKey) {
      const stopVal = brief[stopKey];
      const stopped = stopVal === true || stopVal === "是" || stopVal === "True";
      const prod = String(brief["生产"] || brief["生产影响"] || "");
      const oee = /OEE\s*=\s*([\d.]+)%/i.exec(prod);
      let s = stopped ? "已确认停线" : "未确认停线（无需应急）";
      if (oee) s += `，当前设备效率 ${oee[1]}%`;
      if (eta) s += `，建议约 ${eta} 小时恢复`;
      return s;
    }
    const rep = String((result && result.report) || "").trim();
    if (rep) {
      const first = rep.replace(/^(尊敬的客户[：:]\s*)/, "")
        .split(/[。\n]/).map((x) => x.trim()).filter(Boolean)[0];
      if (first) return first.length > 64 ? first.slice(0, 64) + "…" : first;
    }
    const vals = Object.keys(brief)
      .filter((k) => !k.endsWith("#structured") && k !== "数据缺口")
      .map((k) => brief[k])
      .filter((v) => v && v !== "—");
    return vals.slice(0, 2).join("；") || "已完成分析，详见下方结论";
  }

  /** 预计恢复时间（小时）文本，无则空串 */
  function etaText(result) {
    const brief = (result && result.internal_brief) || {};
    const text = String(brief["FA评级"] || brief["风险评级"] || brief["生产"] || "");
    const m = /预计\s*(\d+(?:\.\d+)?)\s*h/i.exec(text);
    return m ? m[1] : "";
  }

  /** 证据来源业务口径（A2：表名 → 系统名，去重保序） */
  function originLabel(items) {
    const seen = [];
    (items || []).forEach((p) => {
      (p && p.tables ? p.tables : []).forEach((t) => {
        const name = TABLE_LABELS[t] || t;
        if (name && seen.indexOf(name) < 0) seen.push(name);
      });
    });
    return seen.length ? "数据来源：" + seen.join(" / ") : "";
  }

  /* ============================================================
   * 五、多角色信息分层（A6）——仅展示层过滤，后端权限仍为权威
   * ============================================================ */

  /** 视图分桶（按岗位 id → 视图；未登记岗位落 all） */
  const ROLE_VIEW = {
    gm: "mgmt", qd: "mgmt", finm: "mgmt",
    mfg: "shop", eqm: "shop",
    pln: "plan", mc: "plan", scm: "plan", proc: "plan", procm: "plan",
    sqe: "quality", pqe: "quality", qa: "quality",
    eq: "equip", rdm: "equip", rd: "equip", pe: "equip",
    wh: "plan", whm: "plan", biz: "mgmt", fin: "mgmt",
  };

  /** 视图 → 首屏主字段（关键词"被 key 包含"即命中；未命中任何视图的 key 对所有人可见） */
  const VIEWS = {
    mgmt: { label: "管理层", primary: ["风险", "对客", "客户沟通", "停线确认", "是否已停线", "影响面", "生产", "可承诺"] },
    shop: { label: "现场", primary: ["停线确认", "是否已停线", "生产", "处置建议", "工单", "复线", "恢复", "换产"] },
    plan: { label: "计划物控", primary: ["排产", "物料", "齐套", "订单", "库存", "仓储", "在途", "物流", "产出", "采购", "供应链"] },
    quality: { label: "质量", primary: ["8D", "整改", "过程分析", "制程", "供应商", "责任", "缺陷", "品质", "根因", "来料"] },
    equip: { label: "设备工程", primary: ["设备", "维保", "换产", "参数", "备件"] },
    all: { label: "通用", primary: [] },
  };

  const VIEW_ORDER = ["mgmt", "shop", "plan", "quality", "equip"];

  function viewOf(roleId) {
    const v = ROLE_VIEW[String(roleId || "").toLowerCase()];
    return v && VIEWS[v] ? v : "all";
  }

  function _claimed(key) {
    return VIEW_ORDER.some((v) => VIEWS[v].primary.some((p) => String(key).indexOf(p) >= 0));
  }

  function _isPrimary(view, key) {
    if (view === "all") return true;
    if (!_claimed(key)) return true; // 无归属的通用字段（如"数据缺口"）所有人可见
    return VIEWS[view].primary.some((p) => String(key).indexOf(p) >= 0);
  }

  /** 简报首键 = 场景首要事实（与 _BRIEF_SPEC 顺序一致），对所有角色始终可见 */
  function _leadKey(keys) { return (keys || [])[0]; }

  /** 本角色首屏字段 */
  function visibleBriefKeys(roleId, keys) {
    const view = viewOf(roleId);
    const lead = _leadKey(keys);
    return (keys || []).filter((k) => k === lead || _isPrimary(view, k));
  }

  /** 折入「其他角色关注字段」的字段（不丢数据） */
  function hiddenBriefKeys(roleId, keys) {
    const view = viewOf(roleId);
    const lead = _leadKey(keys);
    return (keys || []).filter((k) => k !== lead && !_isPrimary(view, k));
  }

  /* ============================================================
   * 六、门面 API
   * ============================================================ */

  function scenarioName(key) {
    const k = String(key || "");
    return SCENARIO_NAMES[k] || k || "未知场景";
  }

  function briefLabel(key) {
    const k = String(key || "");
    return BRIEF_LABELS[k] || k;
  }

  function domainLabel(d) {
    const k = String(d || "");
    return DOMAIN_LABELS[k] || k || "-";
  }

  function channelLabel(c) {
    const k = String(c || "");
    return CHANNEL_LABELS[k] || k || "-";
  }

  function queryLabel(q) {
    const k = String(q || "");
    return QUERY_LABELS[k] || k || "-";
  }

  function tableLabel(t) {
    const k = String(t || "");
    return TABLE_LABELS[k] || k;
  }

  function levelLabel(level) {
    return LEVEL_LABELS[String(level || "")] || String(level || "-");
  }

  function levelName(level) {
    return LEVEL_LABELS_SEVERITY[String(level || "")] || "正常";
  }

  function viewLabel(roleId) {
    const v = viewOf(roleId);
    return (VIEWS[v] && VIEWS[v].label) || "通用";
  }

  /** 数据来源业务口径（接受 provenance[] 或 tables[]） */
  function originLabelOf(items) {
    const list = items || [];
    if (list.length && list[0] && (list[0].tables || list[0].channel)) return originLabel(list);
    return originLabel([{ tables: list }]);
  }

  window.TERMS = {
    SCENARIO_NAMES, LEVEL_LABELS, DOMAIN_LABELS, CHANNEL_LABELS,
    TABLE_LABELS, QUERY_LABELS, BRIEF_LABELS, ROLE_VIEW, VIEWS,
    scenarioName, briefLabel, domainLabel, channelLabel, queryLabel,
    tableLabel, levelLabel, levelName, severity, headline, etaText,
    originLabel, originLabelOf,
    viewOf, viewLabel, visibleBriefKeys, hiddenBriefKeys,
  };
})();
